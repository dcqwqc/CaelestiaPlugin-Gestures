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
import socket
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

TOUCHPAD_NAME = "elan06fa:00 04f3:327e touchpad"
TOUCHPAD_HYPR_NAME = "elan06fa:00-04f3:327e-touchpad"
TOUCHSCREEN_NAME = "wacom hid 53b7 finger"
TOUCHSCREEN_HYPR_NAME = "wacom-hid-53b7-finger"
TOUCHSCREEN_OUTPUT = "eDP-1"
REMOTE = str(Path.home() / ".local/bin/kagami-remote")
UINPUT_MOUSE = str(Path.home() / ".local/libexec/mirai-uinput-mouse")

TAP_MAX_SECONDS = 0.50
TAP_MAX_MOVE = 180.0
DRAG_ARM_SECONDS = 0.38
DRAG_START_MOVE = 70.0
REMOTE_SWIPE_DISTANCE = 500.0
REMOTE_COOLDOWN = 0.85
TWO_PAN_START = 0.008        # normalized centroid travel (~0.8% of pad/screen)
TWO_ZOOM_START_LOG = 0.040    # touchpad: ~4% scale change before zoom locks in
TWO_ZOOM_DOMINANCE = 1.30    # touchpad scale change vs centroid translation
TOUCH_SETTLE_SECONDS = 0.12   # let second contact geometry stabilize first
TOUCH_PAN_COMMIT = 0.006       # ~0.6% translation = intentional canvas move
TOUCH_ZOOM_START_LOG = 0.120   # ~12.7% post-settle spacing change
TOUCH_ZOOM_DOMINANCE = 2.50    # pinch must strongly dominate translation
TOUCH_ZOOM_CONFIRM_FRAMES = 4
TOUCH_DECISION_SECONDS = 0.45  # unresolved gesture defaults to PAN
PINCH_TICKS_PER_LOG = 12.0    # gentler: one wheel notch per ~8.7% scale change
PINCH_TICK_LIMIT = 1          # avoid bursty jumps from one frame


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


def ydotool_sync(*args: str) -> None:
    env = os.environ.copy()
    env.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
    try:
        subprocess.run(
            ["ydotool", *args], env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=0.20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def mouse_click(button: str) -> None:
    code = {"left": "0xC0", "right": "0xC1"}[button]
    ydotool("click", code)


def mouse_left_down() -> None:
    ydotool("click", "0x40")


def mouse_left_up() -> None:
    ydotool("click", "0x80")


def mouse_middle_down() -> None:
    # Blocking is intentional: Aseprite must see button-down before cursor motion.
    ydotool_sync("click", "0x42")


def mouse_middle_up() -> None:
    ydotool_sync("click", "0x82")


_touch_mouse_proc: subprocess.Popen[str] | None = None
_touch_mouse_lock = threading.Lock()


def ensure_touch_mouse() -> subprocess.Popen[str] | None:
    global _touch_mouse_proc
    with _touch_mouse_lock:
        if _touch_mouse_proc is not None and _touch_mouse_proc.poll() is None:
            return _touch_mouse_proc
        try:
            _touch_mouse_proc = subprocess.Popen(
                [UINPUT_MOUSE],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                close_fds=True,
            )
            return _touch_mouse_proc
        except OSError as e:
            log(f"uinput mouse start failed: {e}")
            _touch_mouse_proc = None
            return None


def touch_mouse_command(command: str) -> bool:
    global _touch_mouse_proc
    for _attempt in range(2):
        proc = ensure_touch_mouse()
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            with _touch_mouse_lock:
                _touch_mouse_proc = None
    return False


def touch_mouse_middle_down() -> None:
    touch_mouse_command("D")


def touch_mouse_middle_up() -> None:
    touch_mouse_command("U")


def touch_mouse_move(dx: int, dy: int) -> None:
    if dx or dy:
        touch_mouse_command(f"M {int(dx)} {int(dy)}")


def hypr_socket_path() -> str:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    return f"/run/user/{os.getuid()}/hypr/{sig}/.socket.sock"


def hypr_request(message: str, timeout: float = 0.08) -> str:
    """Small direct Hyprland IPC request; avoids spawning hyprctl per frame."""
    path = hypr_socket_path()
    if not path or not os.path.exists(path):
        return ""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(message.encode())
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode(errors="replace")
    except OSError:
        return ""


def move_cursor(x: int, y: int) -> None:
    hypr_request(f'dispatch hl.dsp.cursor.move({{ x = {int(x)}, y = {int(y)} }})')


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
        if now - _active_cache[0] < 0.03:
            return _active_cache[1]
        value = False
        try:
            data = json.loads(hypr_request("j/activewindow", timeout=0.05) or "{}")
            # Match the application identity, never the mutable window title.
            # A browser tab such as "Aseprite pinch zoom setup" must not engage
            # the bridge and disable normal touchscreen input.
            haystack = " ".join(
                str(data.get(k, "")) for k in ("class", "initialClass")
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
        except (ValueError, json.JSONDecodeError):
            value = False
        _active_cache = (now, value)
        return value


_touch_policy_lock = threading.Lock()
_touch_disabled_for_aseprite = False


def configured_touchpad_scroll_factor() -> float:
    path = Path.home() / ".config/hypr/variables.lua"
    try:
        import re
        match = re.search(r"touchpadScrollFactor\s*=\s*([0-9.]+)", path.read_text())
        if match:
            return float(match.group(1))
    except (OSError, ValueError):
        pass
    return 0.3


def hypr_eval(expr: str, label: str) -> bool:
    """Apply one live Lua config mutation and make failures visible."""
    try:
        p = subprocess.run(
            ["hyprctl", "eval", expr],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=0.50, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"hypr eval failed {label}: {e}")
        return False

    output = (p.stdout or "").strip()
    if p.returncode != 0 or output.lower() != "ok":
        log(f"hypr eval failed {label}: rc={p.returncode} output={output!r}")
        return False
    return True


def set_touchpad_scroll_factor(value: float) -> bool:
    expr = (
        'hl.device({ name = "' + TOUCHPAD_HYPR_NAME +
        '", scroll_factor = ' + f"{value:g}" + ' })'
    )
    return hypr_eval(expr, f"touchpad scroll_factor={value:g}")


def set_touchscreen_enabled(enabled: bool) -> bool:
    value = "true" if enabled else "false"
    expr = (
        'hl.device({ name = "' + TOUCHSCREEN_HYPR_NAME +
        '", enabled = ' + value + ' })'
    )
    return hypr_eval(expr, f"touchscreen enabled={value}")


def touch_policy_loop() -> None:
    global _touch_disabled_for_aseprite
    native_scroll = configured_touchpad_scroll_factor()
    while True:
        want_bridge = aseprite_active()
        with _touch_policy_lock:
            if want_bridge != _touch_disabled_for_aseprite:
                # Direct touchscreen is fully owned by the daemon in Aseprite.
                touch_ok = set_touchscreen_enabled(not want_bridge)

                # Native two-finger touchpad scrolling looks like mouse-wheel
                # input to Aseprite and therefore zooms. Zero it only in
                # Aseprite; raw MT data still reaches this daemon, which cleanly
                # separates translation (pan) from scale change (zoom).
                scroll_ok = set_touchpad_scroll_factor(0.0 if want_bridge else native_scroll)

                # Only advance policy state when the critical touchscreen
                # mutation succeeded. Otherwise retry instead of claiming the
                # Aseprite bridge is active when raw touch still leaks through.
                if touch_ok:
                    _touch_disabled_for_aseprite = want_bridge
                    log(
                        (
                            "Aseprite bridge -> touchscreen raw + touchpad raw 2-finger"
                            if want_bridge
                            else "input -> native Hyprland"
                        )
                        + (" (touchpad scroll updated)" if scroll_ok else " (touchpad scroll update FAILED)")
                    )
        time.sleep(0.03)


def touchscreen_logical_geometry() -> tuple[int, int, int, int, int]:
    """Return x, y, logical width, logical height, transform for eDP-1."""
    try:
        for mon in json.loads(hypr_request("j/monitors", timeout=0.08) or "[]"):
            if mon.get("name") == TOUCHSCREEN_OUTPUT:
                scale = float(mon.get("scale") or 1.0)
                return (
                    int(mon.get("x") or 0),
                    int(mon.get("y") or 0),
                    max(1, round(float(mon.get("width") or 1) / scale)),
                    max(1, round(float(mon.get("height") or 1) / scale)),
                    int(mon.get("transform") or 0),
                )
    except (ValueError, json.JSONDecodeError):
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

        # Two-finger gesture classifier. "pan" and "zoom" are mutually
        # exclusive for the lifetime of one contact sequence.
        self.two_mode: str | None = None
        self.two_start_centroid: tuple[float, float] | None = None
        self.two_last_centroid: tuple[float, float] | None = None
        self.two_start_distance: float | None = None
        self.two_last_distance: float | None = None
        self.two_zoom_candidate_frames = 0
        self.two_pan_committed = False
        self.two_started_at = 0.0
        self.two_settled = False
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
        self.pan_last_centroid: tuple[float, float] | None = None
        self.touch_geometry = (0, 0, 1440, 900, 0)

    def state_for(self, idx: int) -> dict[str, int | None]:
        return self.slots.setdefault(idx, {"id": None, "x": None, "y": None})

    def norm_point(self, x: float, y: float) -> tuple[float, float]:
        return (
            (x - self.abs_x_min) / max(1, self.abs_x_max - self.abs_x_min),
            (y - self.abs_y_min) / max(1, self.abs_y_max - self.abs_y_min),
        )

    @staticmethod
    def current_cursor() -> tuple[int, int]:
        try:
            x, y = hypr_request("cursorpos", timeout=0.05).strip().split(",", 1)
            return int(float(x)), int(float(y.strip()))
        except ValueError:
            return (0, 0)

    def pan_button_down(self) -> None:
        if self.kind == "touchscreen":
            touch_mouse_middle_down()
        else:
            mouse_middle_down()

    def pan_button_up(self) -> None:
        if self.kind == "touchscreen":
            touch_mouse_middle_up()
        else:
            mouse_middle_up()

    def pan_delta_to_cursor(self, dx: float, dy: float) -> None:
        _, _, width, height, _ = self.touch_geometry
        px = dx / max(1, self.abs_x_max - self.abs_x_min) * width
        py = dy / max(1, self.abs_y_max - self.abs_y_min) * height
        cx, cy = self.current_cursor()
        move_cursor(round(cx + px), round(cy + py))

    def pan_delta_to_uinput(self, dx: float, dy: float) -> None:
        _, _, width, height, transform = self.touch_geometry
        ndx = dx / max(1, self.abs_x_max - self.abs_x_min)
        ndy = dy / max(1, self.abs_y_max - self.abs_y_min)
        if transform == 1:
            px, py = -ndy * width, ndx * height
        elif transform == 2:
            px, py = -ndx * width, -ndy * height
        elif transform == 3:
            px, py = ndy * width, -ndx * height
        else:
            px, py = ndx * width, ndy * height
        touch_mouse_move(round(px), round(py))

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
            self.pan_button_up()
            self.pan_active = False
            log(f"{self.kind} pan -> end")

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
        self.two_mode = None
        self.two_start_centroid = None
        self.two_last_centroid = None
        self.two_start_distance = None
        self.two_last_distance = None
        self.two_zoom_candidate_frames = 0
        self.two_pan_committed = False
        self.two_started_at = 0.0
        self.two_settled = False
        self.remote_fired = False
        self.drag_candidate = False
        self.dragging = False
        self.pan_active = False
        self.pan_last_emit = 0.0
        self.pan_last_centroid = None

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
            self.pan_button_down()
            self.pan_active = True
            self.pan_last_emit = now
            self.pan_last_centroid = centroid
            log("touchscreen one-finger -> Aseprite pan (immediate)")

    @staticmethod
    def centroid(positioned: list[dict[str, int | None]]) -> tuple[float, float] | None:
        if not positioned:
            return None
        return (
            sum(int(s["x"]) for s in positioned) / len(positioned),
            sum(int(s["y"]) for s in positioned) / len(positioned),
        )

    def handle_two_finger(self, positioned: list[dict[str, int | None]]) -> None:
        if len(positioned) != 2 or not aseprite_active():
            return

        a, b = positioned
        ax, ay = float(a["x"]), float(a["y"])
        bx, by = float(b["x"]), float(b["y"])
        cx, cy = (ax + bx) / 2.0, (ay + by) / 2.0

        nax, nay = self.norm_point(ax, ay)
        nbx, nby = self.norm_point(bx, by)
        ncx, ncy = (nax + nbx) / 2.0, (nay + nby) / 2.0
        distance = math.hypot(nax - nbx, nay - nby)
        if distance <= 1e-6:
            return

        if self.two_start_centroid is None or self.two_start_distance is None:
            self.two_start_centroid = (ncx, ncy)
            self.two_last_centroid = (cx, cy)
            self.two_start_distance = distance
            self.two_last_distance = distance
            self.pinch_total_log = 0.0
            self.two_started_at = time.monotonic()
            self.two_settled = self.kind != "touchscreen"
            self.touch_geometry = touchscreen_logical_geometry()

            if self.kind == "touchscreen":
                # Direct touch should feel immediate. Start as canvas pan as
                # soon as the second finger is established; a deliberate scale
                # change can still promote this gesture to zoom below.
                if not self.pan_active:
                    px, py = self.touch_to_cursor(round(cx), round(cy))
                    move_cursor(px, py)
                    self.pan_button_down()
                    self.pan_active = True
                self.pan_last_centroid = (cx, cy)
                self.two_mode = "pan"
                log("touchscreen two-finger -> PAN immediate")
            return

        sx, sy = self.two_start_centroid
        centroid_travel = math.hypot(ncx - sx, ncy - sy)
        total_scale_log = math.log(distance / self.two_start_distance)

        # Direct-touch contacts jump slightly while the second fingertip is
        # settling. Ignore that geometry for a short window, then rebase once.
        # After rebasing, the first clear intent wins and NEVER changes mode
        # until all contacts lift.
        if self.kind == "touchscreen" and self.two_mode == "pan":
            now = time.monotonic()
            age = now - self.two_started_at

            if not self.two_settled:
                if age < TOUCH_SETTLE_SECONDS:
                    self.two_last_centroid = (cx, cy)
                    self.two_last_distance = distance
                    return
                self.two_settled = True
                self.two_start_centroid = (ncx, ncy)
                self.two_start_distance = distance
                self.two_last_centroid = (cx, cy)
                self.two_last_distance = distance
                self.two_zoom_candidate_frames = 0
                log("touchscreen two-finger geometry settled")
                return

            sx, sy = self.two_start_centroid
            centroid_travel = math.hypot(ncx - sx, ncy - sy)
            total_scale_log = math.log(distance / self.two_start_distance)
            zoom_strength = abs(total_scale_log)

            # Translation has priority. A real two-finger drag should lock PAN
            # before ordinary spacing jitter gets any opportunity to look like
            # a pinch.
            if not self.two_pan_committed and centroid_travel >= TOUCH_PAN_COMMIT:
                self.two_pan_committed = True
                self.two_zoom_candidate_frames = 0
                log(
                    f"touchscreen PAN locked "
                    f"translate={centroid_travel:.3f} scale={zoom_strength:.3f}"
                )

            if not self.two_pan_committed:
                looks_like_pinch = (
                    zoom_strength >= TOUCH_ZOOM_START_LOG
                    and zoom_strength >= centroid_travel * TOUCH_ZOOM_DOMINANCE
                )
                self.two_zoom_candidate_frames = (
                    self.two_zoom_candidate_frames + 1 if looks_like_pinch else 0
                )

                if self.two_zoom_candidate_frames >= TOUCH_ZOOM_CONFIRM_FRAMES:
                    self.two_mode = "zoom"
                    self.pinch_active = True
                    self.release_pan()
                    self.pinch_accum = 0.0
                    self.two_last_distance = distance
                    log(
                        f"touchscreen ZOOM locked "
                        f"scale={zoom_strength:.3f} translate={centroid_travel:.3f}"
                    )
                elif age >= TOUCH_DECISION_SECONDS:
                    self.two_pan_committed = True
                    self.two_zoom_candidate_frames = 0
                    log("touchscreen PAN locked by decision timeout")

        # Touchpad still waits for classification, because native touchpad
        # movement is much noisier and accidental pan should be avoided.
        if self.two_mode is None:
            zoom_strength = abs(total_scale_log)
            if (
                zoom_strength >= TWO_ZOOM_START_LOG
                and zoom_strength >= centroid_travel * TWO_ZOOM_DOMINANCE
            ):
                self.two_mode = "zoom"
                self.pinch_active = True
                self.release_pan()
                self.pinch_accum = 0.0
                log(
                    f"{self.kind} two-finger -> ZOOM "
                    f"scale={zoom_strength:.3f} translate={centroid_travel:.3f}"
                )
            elif (
                centroid_travel >= TWO_PAN_START
                and centroid_travel >= zoom_strength / TWO_ZOOM_DOMINANCE
            ):
                self.two_mode = "pan"
                self.pinch_active = False
                if not self.pan_active:
                    self.pan_button_down()
                    self.pan_active = True
                log(
                    f"{self.kind} two-finger -> PAN "
                    f"translate={centroid_travel:.3f} scale={zoom_strength:.3f}"
                )

        if self.two_mode == "zoom":
            if self.two_last_distance and self.two_last_distance > 1e-6:
                delta = math.log(distance / self.two_last_distance)
                self.pinch_accum += delta * PINCH_TICKS_PER_LOG
                ticks = int(self.pinch_accum)
                if ticks:
                    ticks = max(-PINCH_TICK_LIMIT, min(PINCH_TICK_LIMIT, ticks))
                    self.pinch_accum -= ticks
                    if self.kind == "touchscreen":
                        px, py = self.touch_to_cursor(round(cx), round(cy))
                        move_cursor(px, py)
                    wheel(ticks)

        elif self.two_mode == "pan":
            if self.two_last_centroid is not None:
                last_cx, last_cy = self.two_last_centroid
                dx, dy = cx - last_cx, cy - last_cy
                if self.kind == "touchscreen":
                    if self.two_settled and self.two_pan_committed:
                        self.pan_delta_to_uinput(dx, dy)
                else:
                    self.pan_delta_to_cursor(dx, dy)

        self.two_last_centroid = (cx, cy)
        self.two_last_distance = distance


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
            if self.max_fingers == 2:
                # Keep the touchscreen middle-button grab held while a second
                # finger joins. This makes 1->2 finger panning continuous.
                if self.kind != "touchscreen":
                    self.release_pan()
                self.two_mode = None
                self.two_start_centroid = None
                self.two_last_centroid = None
                self.two_start_distance = None
                self.two_last_distance = None
                self.two_zoom_candidate_frames = 0
                self.two_pan_committed = False
                self.two_started_at = 0.0
                self.two_settled = False
                self.pinch_active = False
                self.pinch_accum = 0.0

        if c is not None and self.start_centroid is not None and len(active) == self.max_fingers:
            move = math.hypot(c[0] - self.start_centroid[0], c[1] - self.start_centroid[1])
            self.max_move = max(self.max_move, move)

            if self.kind == "touchscreen" and aseprite_active():
                if len(active) == 1 and len(positioned) == 1:
                    if self.pan_active and now - self.pan_last_emit >= 0.004:
                        if self.pan_last_centroid is not None:
                            last_x, last_y = self.pan_last_centroid
                            self.pan_delta_to_uinput(c[0] - last_x, c[1] - last_y)
                        self.pan_last_centroid = c
                        self.pan_last_emit = now
                elif self.pan_active and (
                    len(active) >= 3 or self.two_mode == "zoom"
                ):
                    # Keep BTN_MIDDLE held continuously across the 1 -> 2
                    # finger transition. Only a confirmed zoom or 3+ contacts
                    # may release the canvas-pan grab.
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
            self.handle_two_finger(positioned)
        elif len(active) != 2:
            self.pinch_last_distance = None

    def watch(self, device: str) -> None:
        self.slot = 0
        self.slots.clear()
        self.reset_session()
        with open(device, "rb", buffering=0) as f:
            try:
                self.abs_x_min, self.abs_x_max = read_abs_range(f.fileno(), ABS_MT_POSITION_X)
                self.abs_y_min, self.abs_y_max = read_abs_range(f.fileno(), ABS_MT_POSITION_Y)
                log(
                    f"{self.kind} abs x={self.abs_x_min}..{self.abs_x_max} "
                    f"y={self.abs_y_min}..{self.abs_y_max}"
                )
            except OSError as e:
                log(f"{self.kind} abs range read failed: {e}")
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
    ensure_touch_mouse()
    time.sleep(0.08)
    hypr_eval(
        'hl.device({ name = "mirai-gesture-mouse", accel_profile = "flat", sensitivity = 0.0 })',
        "gesture mouse flat profile",
    )
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
        set_touchpad_scroll_factor(configured_touchpad_scroll_factor())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", default="daemon", choices=["daemon", "status-json", "toggle", "start", "stop", "restart"])
    ns = ap.parse_args()
    if ns.command == "daemon":
        daemon(); return 0
    return control(ns.command)


if __name__ == "__main__":
    raise SystemExit(main())
