#!/usr/bin/env python3
"""Mirai's low-level touchpad/touchscreen gesture bridge.

Design constraints:
- never EVIOCGRAB input devices; native movement/scroll/pen input stays native
- touchpad taps are re-emitted because native tap_to_click is disabled on Mirai
  so 3-finger tap can mean Undo without also producing a middle click
- preserve the existing four-finger Remote Desktop swipe
- only synthesize pinch-wheel events while Aseprite is focused
"""
from __future__ import annotations

import argparse
import glob
import json
import fcntl
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time

EVENT = struct.Struct("llHHi")
EV_SYN = 0x00
EV_ABS = 0x03
SYN_REPORT = 0
ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TRACKING_ID = 0x39

TOUCHPAD_NAME = "touchpad"
TOUCHSCREEN_NAME = "wacom hid 53b7 finger"
TOUCHSCREEN_HYPR_NAME = "wacom-hid-53b7-finger"
TOUCHSCREEN_OUTPUT = "eDP-1"
REMOTE = str(Path.home() / ".local/bin/kagami-remote")

TAP_MAX_SECONDS = 0.50
TAP_MAX_MOVE = 180.0
DRAG_ARM_SECONDS = 0.38
DRAG_START_MOVE = 70.0
REMOTE_SWIPE_DISTANCE = 500.0
REMOTE_COOLDOWN = 0.85
PINCH_START_LOG = 0.018       # ~1.8% scale change before zoom engages
PINCH_TICKS_PER_LOG = 30.0    # about one wheel notch per ~3.3% scale change
PINCH_TICK_LIMIT = 3          # avoid bursty jumps from one frame


def log(message: str) -> None:
    print(f"[mirai-gestures] {time.monotonic():.3f} {message}", flush=True)


def quiet_popen(argv: list[str]) -> None:
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        pass


def ydotool(*args: str) -> None:
    env = os.environ.copy()
    env.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
    try:
        subprocess.Popen(["ydotool", *args], env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except OSError:
        pass


def mouse_click(button: str) -> None:
    code = {"left": "0xC0", "right": "0xC1"}[button]
    ydotool("click", code)


def mouse_left_down() -> None:
    ydotool("click", "0x40")


def mouse_left_up() -> None:
    ydotool("click", "0x80")


def mouse_middle_down() -> None:
    ydotool("click", "0x42")


def mouse_middle_up() -> None:
    ydotool("click", "0x82")


def move_cursor(x: int, y: int) -> None:
    expr = f'hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})'
    quiet_popen(["hyprctl", "dispatch", expr])


def wheel(ticks: int) -> None:
    if not ticks:
        return
    # ydotool wheel mode takes positional x/y deltas after "--".
    ydotool("mousemove", "--wheel", "--", "0", str(ticks))
    log(f"wheel ticks={ticks}")


def hypr_shortcut(key: str) -> None:
    # External dispatcher path is reliable on Hyprland 0.56 for both native
    # Wayland and XWayland clients.
    expr = f'hl.dsp.send_shortcut({{ mods = "CTRL", key = "{key}" }})'
    quiet_popen(["hyprctl", "dispatch", expr])
    log(f"shortcut ctrl+{key}")


def toggle_remote() -> None:
    if os.path.exists(REMOTE):
        quiet_popen([REMOTE, "peer", "toggle"])


_active_cache: tuple[float, bool] = (0.0, False)
_active_lock = threading.Lock()


def aseprite_active() -> bool:
    global _active_cache
    now = time.monotonic()
    with _active_lock:
        if now - _active_cache[0] < 0.20:
            return _active_cache[1]
        value = False
        try:
            p = subprocess.run(
                ["hyprctl", "-j", "activewindow"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=0.20, check=False,
            )
            data = json.loads(p.stdout or "{}")
            haystack = " ".join(
                str(data.get(k, "")) for k in ("class", "initialClass", "title", "initialTitle")
            ).lower()
            value = "aseprite" in haystack
            if not value:
                pid = int(data.get("pid") or 0)
                if pid > 0:
                    parts = []
                    for f in (Path(f"/proc/{pid}/comm"), Path(f"/proc/{pid}/cmdline")):
                        try:
                            raw = f.read_bytes().replace(bytes([0]), b" ")
                            parts.append(raw.decode(errors="replace"))
                        except OSError:
                            pass
                    value = "aseprite" in " ".join(parts).lower()
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            value = False
        _active_cache = (now, value)
        return value


_touch_policy_lock = threading.Lock()
_touch_disabled_for_aseprite = False


def set_touchscreen_enabled(enabled: bool) -> None:
    value = "true" if enabled else "false"
    expr = f'hl.device({{ name = "{TOUCHSCREEN_HYPR_NAME}", enabled = {value} }})'
    try:
        subprocess.run(
            ["hyprctl", "repl", expr],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=0.35, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def touch_policy_loop() -> None:
    global _touch_disabled_for_aseprite
    while True:
        want_disabled = aseprite_active()
        with _touch_policy_lock:
            if want_disabled != _touch_disabled_for_aseprite:
                set_touchscreen_enabled(not want_disabled)
                _touch_disabled_for_aseprite = want_disabled
                log(
                    "touchscreen -> raw Aseprite bridge"
                    if want_disabled
                    else "touchscreen -> native Hyprland"
                )
        time.sleep(0.10)


def touchscreen_logical_geometry() -> tuple[int, int, int, int, int]:
    """Return x, y, logical width, logical height, transform for eDP-1."""
    try:
        p = subprocess.run(
            ["hyprctl", "-j", "monitors"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=0.25, check=False,
        )
        for mon in json.loads(p.stdout or "[]"):
            if mon.get("name") == TOUCHSCREEN_OUTPUT:
                scale = float(mon.get("scale") or 1.0)
                return (
                    int(mon.get("x") or 0),
                    int(mon.get("y") or 0),
                    max(1, round(float(mon.get("width") or 1) / scale)),
                    max(1, round(float(mon.get("height") or 1) / scale)),
                    int(mon.get("transform") or 0),
                )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        pass
    return (0, 0, 1440, 900, 0)


def read_abs_range(fd: int, code: int) -> tuple[int, int]:
    # EVIOCGABS(code), struct input_absinfo = six signed ints.
    cmd = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + code)
    data = bytearray(24)
    fcntl.ioctl(fd, cmd, data, True)
    _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack("iiiiii", data)
    return minimum, maximum


def find_device(name_fragment: str) -> str | None:
    for dev in sorted(glob.glob("/dev/input/event*")):
        event = os.path.basename(dev)
        name_file = Path("/sys/class/input") / event / "device/name"
        try:
            name = name_file.read_text(errors="replace").strip().lower()
        except OSError:
            continue
        if name_fragment in name:
            return dev
    return None


class MTWatcher:
    def __init__(self, kind: str):
        self.kind = kind
        self.name_fragment = TOUCHPAD_NAME if kind == "touchpad" else TOUCHSCREEN_NAME
        self.slot = 0
        self.slots: dict[int, dict[str, int | None]] = {}
        self.session = False
        self.started = 0.0
        self.max_fingers = 0
        self.start_centroid: tuple[float, float] | None = None
        self.max_move = 0.0
        self.pinch_last_distance: float | None = None
        self.pinch_total_log = 0.0
        self.pinch_active = False
        self.pinch_accum = 0.0
        self.remote_fired = False
        self.last_remote = 0.0
        self.last_single_tap = -999.0
        self.drag_candidate = False
        self.dragging = False

        # Direct touchscreen -> Aseprite bridge state.
        self.abs_x_min = 0
        self.abs_x_max = 1
        self.abs_y_min = 0
        self.abs_y_max = 1
        self.pan_active = False
        self.pan_last_emit = 0.0
        self.touch_geometry = (0, 0, 1440, 900, 0)

    def state_for(self, idx: int) -> dict[str, int | None]:
        return self.slots.setdefault(idx, {"id": None, "x": None, "y": None})

    def touch_to_cursor(self, x: int, y: int) -> tuple[int, int]:
        nx = (x - self.abs_x_min) / max(1, self.abs_x_max - self.abs_x_min)
        ny = (y - self.abs_y_min) / max(1, self.abs_y_max - self.abs_y_min)
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        ox, oy, width, height, transform = self.touch_geometry

        # wl_output transforms 0/1/2/3 are normal/90/180/270.
        if transform == 1:
            nx, ny = 1.0 - ny, nx
        elif transform == 2:
            nx, ny = 1.0 - nx, 1.0 - ny
        elif transform == 3:
            nx, ny = ny, 1.0 - nx

        return (
            ox + round(nx * max(1, width - 1)),
            oy + round(ny * max(1, height - 1)),
        )

    def release_pan(self) -> None:
        if self.pan_active:
            mouse_middle_up()
            self.pan_active = False
            log("touchscreen pan -> end")

    def reset_session(self) -> None:
        self.session = False
        self.started = 0.0
        self.max_fingers = 0
        self.start_centroid = None
        self.max_move = 0.0
        self.pinch_last_distance = None
        self.pinch_total_log = 0.0
        self.pinch_active = False
        self.pinch_accum = 0.0
        self.remote_fired = False
        self.drag_candidate = False
        self.dragging = False
        self.pan_active = False
        self.pan_last_emit = 0.0

    def begin(self, active: list[dict[str, int | None]], centroid: tuple[float, float] | None) -> None:
        now = time.monotonic()
        self.session = True
        self.started = now
        self.max_fingers = len(active)
        self.start_centroid = centroid
        self.max_move = 0.0
        self.drag_candidate = self.kind == "touchpad" and len(active) == 1 and now - self.last_single_tap <= DRAG_ARM_SECONDS
        if self.kind == "touchscreen" and len(active) == 1 and centroid is not None and aseprite_active():
            self.touch_geometry = touchscreen_logical_geometry()
            cx, cy = self.touch_to_cursor(round(centroid[0]), round(centroid[1]))
            move_cursor(cx, cy)

    @staticmethod
    def centroid(positioned: list[dict[str, int | None]]) -> tuple[float, float] | None:
        if not positioned:
            return None
        return (
            sum(int(s["x"]) for s in positioned) / len(positioned),
            sum(int(s["y"]) for s in positioned) / len(positioned),
        )

    def handle_pinch(self, positioned: list[dict[str, int | None]]) -> None:
        if len(positioned) != 2:
            self.pinch_last_distance = None
            return
        a, b = positioned
        if self.kind == "touchscreen" and aseprite_active():
            cx = (int(a["x"]) + int(b["x"])) // 2
            cy = (int(a["y"]) + int(b["y"])) // 2
            px, py = self.touch_to_cursor(cx, cy)
            move_cursor(px, py)

        distance = math.hypot(int(a["x"]) - int(b["x"]), int(a["y"]) - int(b["y"]))
        if distance < 1.0:
            return
        if self.pinch_last_distance is None:
            self.pinch_last_distance = distance
            return
        delta = math.log(distance / self.pinch_last_distance)
        self.pinch_last_distance = distance
        # Tiny width changes happen during ordinary two-finger scroll. Require a
        # real cumulative scale change before converting anything to wheel.
        self.pinch_total_log += delta
        if not self.pinch_active:
            if abs(self.pinch_total_log) < PINCH_START_LOG:
                return
            if not aseprite_active():
                return
            self.pinch_active = True
            log(f"{self.kind} pinch -> Aseprite zoom engaged")
            self.pinch_accum = self.pinch_total_log * PINCH_TICKS_PER_LOG
            # An intentional short pinch must still visibly do something.
            if abs(self.pinch_accum) < 1.0:
                self.pinch_accum = math.copysign(1.0, self.pinch_total_log)
        else:
            if not aseprite_active():
                self.pinch_active = False
                self.pinch_accum = 0.0
                return
            self.pinch_accum += delta * PINCH_TICKS_PER_LOG

        ticks = int(self.pinch_accum)
        if ticks:
            ticks = max(-PINCH_TICK_LIMIT, min(PINCH_TICK_LIMIT, ticks))
            self.pinch_accum -= ticks
            wheel(ticks)

    def finish(self, now: float) -> None:
        duration = now - self.started
        if self.pan_active:
            self.release_pan()
            self.last_single_tap = -999.0
            self.reset_session()
            return
        if self.dragging:
            mouse_left_up()
            self.last_single_tap = -999.0
            self.reset_session()
            return

        is_tap = (
            duration <= TAP_MAX_SECONDS
            and self.max_move <= TAP_MAX_MOVE
            and not self.pinch_active
            and not self.remote_fired
        )
        fingers = self.max_fingers
        if fingers >= 3 and not is_tap:
            log(f"{self.kind} {fingers}-finger end rejected: duration={duration:.3f}s move={self.max_move:.1f} pinch={self.pinch_active} remote={self.remote_fired}")
        if is_tap:
            if self.kind == "touchpad" and fingers == 1:
                mouse_click("left")
                log("touchpad tap1 -> left click")
                self.last_single_tap = now
            elif self.kind == "touchpad" and fingers == 2:
                mouse_click("right")
                log("touchpad tap2 -> right click")
                self.last_single_tap = -999.0
            elif fingers == 3:
                hypr_shortcut("z")
                log(f"{self.kind} tap3 -> undo")
                self.last_single_tap = -999.0
            elif fingers == 4:
                hypr_shortcut("y")
                log(f"{self.kind} tap4 -> redo")
                self.last_single_tap = -999.0
        self.reset_session()

    def report(self) -> None:
        active = [s for s in self.slots.values() if s["id"] is not None]
        positioned = [s for s in active if s["x"] is not None and s["y"] is not None]
        now = time.monotonic()

        if not active:
            if self.session:
                self.finish(now)
            return

        c = self.centroid(positioned) if len(positioned) == len(active) else None
        if not self.session:
            self.begin(active, c)

        # A multi-finger tap arrives one contact at a time. Measuring movement
        # from finger #1's centroid makes the centroid jump merely because
        # fingers #2/#3/#4 were placed, falsely rejecting every multi-finger
        # tap as movement. Rebase when the peak finger count increases, and
        # freeze movement once contacts start lifting.
        if len(active) > self.max_fingers:
            self.max_fingers = len(active)
            if c is not None:
                self.start_centroid = c
                self.max_move = 0.0
            if self.max_fingers > 1:
                self.drag_candidate = False

        if c is not None and self.start_centroid is not None and len(active) == self.max_fingers:
            move = math.hypot(c[0] - self.start_centroid[0], c[1] - self.start_centroid[1])
            self.max_move = max(self.max_move, move)

            if self.kind == "touchscreen" and aseprite_active():
                if len(active) == 1 and len(positioned) == 1:
                    if move >= 35.0 and not self.pan_active:
                        mouse_middle_down()
                        self.pan_active = True
                        log("touchscreen one-finger -> Aseprite pan")
                    if self.pan_active and now - self.pan_last_emit >= 0.008:
                        px, py = self.touch_to_cursor(round(c[0]), round(c[1]))
                        move_cursor(px, py)
                        self.pan_last_emit = now
                elif self.pan_active:
                    self.release_pan()

            if self.kind == "touchpad" and self.drag_candidate and self.max_fingers == 1 and not self.dragging and move >= DRAG_START_MOVE:
                mouse_left_down()
                self.dragging = True

            if self.kind == "touchpad" and len(active) == 4 and not self.remote_fired:
                if move >= REMOTE_SWIPE_DISTANCE and now - self.last_remote >= REMOTE_COOLDOWN:
                    self.remote_fired = True
                    self.last_remote = now
                    toggle_remote()
                    log("touchpad swipe4 -> remote toggle")

        # Pinch zoom is supported on both the touchpad and direct touchscreen.
        # It is Aseprite-only, so normal browser/image pinch behavior elsewhere
        # remains owned by the application/compositor.
        if len(active) == 2 and len(positioned) == 2:
            self.handle_pinch(positioned)
        elif len(active) != 2:
            self.pinch_last_distance = None

    def watch(self, device: str) -> None:
        self.slot = 0
        self.slots.clear()
        self.reset_session()
        with open(device, "rb", buffering=0) as f:
            if self.kind == "touchscreen":
                try:
                    self.abs_x_min, self.abs_x_max = read_abs_range(f.fileno(), ABS_MT_POSITION_X)
                    self.abs_y_min, self.abs_y_max = read_abs_range(f.fileno(), ABS_MT_POSITION_Y)
                    log(
                        f"touchscreen abs x={self.abs_x_min}..{self.abs_x_max} "
                        f"y={self.abs_y_min}..{self.abs_y_max}"
                    )
                except OSError as e:
                    log(f"touchscreen abs range read failed: {e}")
            while True:
                data = f.read(EVENT.size)
                if len(data) != EVENT.size:
                    raise OSError("input event stream ended")
                _sec, _usec, ev_type, code, value = EVENT.unpack(data)
                if ev_type == EV_ABS:
                    if code == ABS_MT_SLOT:
                        self.slot = value
                    elif code == ABS_MT_TRACKING_ID:
                        s = self.state_for(self.slot)
                        if value < 0:
                            s["id"] = None; s["x"] = None; s["y"] = None
                        else:
                            s["id"] = value; s["x"] = None; s["y"] = None
                    elif code == ABS_MT_POSITION_X:
                        self.state_for(self.slot)["x"] = value
                    elif code == ABS_MT_POSITION_Y:
                        self.state_for(self.slot)["y"] = value
                elif ev_type == EV_SYN and code == SYN_REPORT:
                    self.report()

    def run_forever(self) -> None:
        while True:
            device = find_device(self.name_fragment)
            if not device:
                time.sleep(2)
                continue
            try:
                self.watch(device)
            except (OSError, PermissionError):
                if self.dragging:
                    mouse_left_up()
                self.release_pan()
                time.sleep(1)


def service_active() -> bool:
    p = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "mirai-gestures.service"])
    return p.returncode == 0


def control(action: str) -> int:
    if action == "status-json":
        print(json.dumps({"available": True, "active": service_active()}))
        return 0
    if action == "toggle":
        action = "stop" if service_active() else "start"
    return subprocess.call(["systemctl", "--user", action, "mirai-gestures.service"])


def daemon() -> None:
    workers = [MTWatcher("touchpad"), MTWatcher("touchscreen")]
    threads = [threading.Thread(target=w.run_forever, daemon=True, name=f"mirai-{w.kind}") for w in workers]
    policy = threading.Thread(target=touch_policy_loop, daemon=True, name="mirai-touch-policy")
    for t in threads:
        t.start()
    policy.start()
    try:
        while True:
            for t in threads:
                if not t.is_alive():
                    raise RuntimeError(f"gesture worker {t.name} stopped")
            if not policy.is_alive():
                raise RuntimeError("gesture policy worker stopped")
            time.sleep(5)
    finally:
        set_touchscreen_enabled(True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="daemon", choices=["daemon", "status-json", "toggle", "start", "stop", "restart"])
    ns = ap.parse_args()
    if ns.command == "daemon":
        daemon(); return 0
    return control(ns.command)


if __name__ == "__main__":
    raise SystemExit(main())
