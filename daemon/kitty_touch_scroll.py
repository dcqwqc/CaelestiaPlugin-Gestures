#!/usr/bin/env python3
"""Passive one-finger touchscreen scrolling for Kitty on Mirai.

This daemon NEVER grabs or disables the touchscreen. Hyprland continues to own
native touch normally. When the focused window is Kitty and a single direct
finger performs a vertical drag that starts inside that Kitty window, the
movement is translated into ordinary wheel events for Kitty's scrollback.
"""

from __future__ import annotations

import fcntl
import glob
import json
import math
import os
from pathlib import Path
import struct
import subprocess
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

TOUCHSCREEN_NAME = "wacom hid 53b7 finger"
TOUCHSCREEN_OUTPUT = "eDP-1"

# Gesture tuning in logical compositor pixels.
COMMIT_PX = 11.0
VERTICAL_DOMINANCE = 1.20
WHEEL_STEP_PX = 18.0
EMIT_INTERVAL = 0.022
MAX_TICKS_PER_EMIT = 3

STATE_POLL_INTERVAL = 0.08
YDOTOOL_SOCKET = f"/run/user/{os.getuid()}/.ydotool_socket"


def log(message: str) -> None:
    print(f"[kitty-touch-scroll] {time.monotonic():.3f} {message}", flush=True)


def find_device() -> str | None:
    for dev in sorted(glob.glob("/dev/input/event*")):
        name_file = Path("/sys/class/input") / Path(dev).name / "device/name"
        try:
            name = name_file.read_text(errors="replace").strip().lower()
        except OSError:
            continue
        if TOUCHSCREEN_NAME in name:
            return dev
    return None


def read_abs_range(fd: int, code: int) -> tuple[int, int]:
    # EVIOCGABS(code), struct input_absinfo = six signed ints.
    cmd = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + code)
    data = bytearray(24)
    fcntl.ioctl(fd, cmd, data, True)
    _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack(
        "iiiiii", data
    )
    return minimum, maximum


def run_json(*args: str) -> dict | list:
    try:
        proc = subprocess.run(
            ["hyprctl", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=0.12,
            check=False,
        )
        return json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


class DesktopState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.kitty_window: dict | None = None
        self.monitor = {
            "x": 0,
            "y": 0,
            "width": 2880,
            "height": 1800,
            "scale": 2.0,
            "transform": 0,
        }

    def snapshot(self) -> tuple[dict | None, dict]:
        with self.lock:
            window = dict(self.kitty_window) if self.kitty_window else None
            monitor = dict(self.monitor)
        return window, monitor

    def poll_once(self) -> None:
        active = run_json("activewindow", "-j")
        kitty: dict | None = None
        if isinstance(active, dict):
            ident = " ".join(
                str(active.get(key, "")) for key in ("class", "initialClass")
            ).lower()
            if "kitty" in ident:
                kitty = active

        monitors = run_json("monitors", "-j")
        monitor = None
        if isinstance(monitors, list):
            monitor = next(
                (m for m in monitors if m.get("name") == TOUCHSCREEN_OUTPUT),
                None,
            )

        with self.lock:
            self.kitty_window = kitty
            if isinstance(monitor, dict):
                self.monitor = monitor

    def loop(self) -> None:
        last_kitty = False
        while True:
            self.poll_once()
            with self.lock:
                is_kitty = self.kitty_window is not None
            if is_kitty != last_kitty:
                log("Kitty focus -> on" if is_kitty else "Kitty focus -> off")
                last_kitty = is_kitty
            time.sleep(STATE_POLL_INTERVAL)


def logical_point(
    raw_x: float,
    raw_y: float,
    x_min: int,
    x_max: int,
    y_min: int,
    y_max: int,
    monitor: dict,
) -> tuple[float, float]:
    nx = (raw_x - x_min) / max(1, x_max - x_min)
    ny = (raw_y - y_min) / max(1, y_max - y_min)
    nx = min(1.0, max(0.0, nx))
    ny = min(1.0, max(0.0, ny))

    transform = int(monitor.get("transform") or 0)
    if transform == 1:
        tx, ty = 1.0 - ny, nx
    elif transform == 2:
        tx, ty = 1.0 - nx, 1.0 - ny
    elif transform == 3:
        tx, ty = ny, 1.0 - nx
    else:
        tx, ty = nx, ny

    scale = float(monitor.get("scale") or 1.0)
    width = max(1.0, float(monitor.get("width") or 1.0) / scale)
    height = max(1.0, float(monitor.get("height") or 1.0) / scale)
    ox = float(monitor.get("x") or 0)
    oy = float(monitor.get("y") or 0)

    return ox + tx * width, oy + ty * height


def point_inside_window(x: float, y: float, window: dict) -> bool:
    at = window.get("at") or [0, 0]
    size = window.get("size") or [0, 0]
    try:
        wx, wy = float(at[0]), float(at[1])
        ww, wh = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return False
    return wx <= x < wx + ww and wy <= y < wy + wh


def move_cursor(x: float, y: float) -> None:
    expr = f"hl.dsp.cursor.move({{ x = {round(x)}, y = {round(y)} }})"
    try:
        subprocess.run(
            ["hyprctl", "dispatch", expr],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def wheel(ticks: int) -> None:
    if not ticks:
        return
    env = os.environ.copy()
    env.setdefault("YDOTOOL_SOCKET", YDOTOOL_SOCKET)
    try:
        subprocess.Popen(
            ["ydotool", "mousemove", "--wheel", "--", "0", str(ticks)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass


class KittyTouchScroll:
    def __init__(self, desktop: DesktopState) -> None:
        self.desktop = desktop
        self.slot = 0
        self.slots: dict[int, dict[str, int | None]] = {}

        self.x_min = 0
        self.x_max = 30181
        self.y_min = 0
        self.y_max = 18863

        self.tracking = False
        self.committed = False
        self.started_inside = False
        self.start_x = 0.0
        self.start_y = 0.0
        self.last_y = 0.0
        self.accum_y = 0.0
        self.last_emit = 0.0
        self.total_ticks = 0

    def state_for(self, slot: int) -> dict[str, int | None]:
        return self.slots.setdefault(slot, {"id": None, "x": None, "y": None})

    def finish(self) -> None:
        if self.committed:
            log(f"one-finger Kitty scroll -> end ticks={self.total_ticks}")
        self.tracking = False
        self.committed = False
        self.started_inside = False
        self.accum_y = 0.0
        self.total_ticks = 0

    def report(self) -> None:
        active = [s for s in self.slots.values() if s["id"] is not None]
        positioned = [
            s for s in active if s["x"] is not None and s["y"] is not None
        ]

        # Only one-finger direct touch becomes terminal scrolling.
        # Two or more fingers remain entirely native and are ignored here.
        if len(active) != 1 or len(positioned) != 1:
            if self.tracking:
                self.finish()
            return

        window, monitor = self.desktop.snapshot()
        if window is None:
            if self.tracking:
                self.finish()
            return

        raw_x = float(positioned[0]["x"])
        raw_y = float(positioned[0]["y"])
        x, y = logical_point(
            raw_x,
            raw_y,
            self.x_min,
            self.x_max,
            self.y_min,
            self.y_max,
            monitor,
        )

        if not self.tracking:
            self.tracking = True
            self.started_inside = point_inside_window(x, y, window)
            self.start_x = x
            self.start_y = y
            self.last_y = y
            self.accum_y = 0.0
            self.total_ticks = 0
            self.last_emit = 0.0
            return

        if not self.started_inside:
            self.last_y = y
            return

        dx = x - self.start_x
        dy = y - self.start_y

        if not self.committed:
            if abs(dy) < COMMIT_PX:
                self.last_y = y
                return
            if abs(dy) < abs(dx) * VERTICAL_DOMINANCE:
                self.last_y = y
                return
            if not point_inside_window(x, y, window):
                self.finish()
                return

            # Wheel events are pointer-routed on Wayland. Move the pointer to the
            # actual finger position once when the drag commits so the synthetic
            # wheel event is guaranteed to target Kitty. We do not move it per
            # frame and we do not synthesize any clicks.
            move_cursor(x, y)
            self.committed = True
            self.accum_y = dy
            self.last_y = y
            self.last_emit = time.monotonic()
            log("one-finger Kitty scroll -> committed")
        else:
            self.accum_y += y - self.last_y
            self.last_y = y

        now = time.monotonic()
        if now - self.last_emit < EMIT_INTERVAL:
            return

        ticks = math.trunc(self.accum_y / WHEEL_STEP_PX)
        if not ticks:
            return

        ticks = max(-MAX_TICKS_PER_EMIT, min(MAX_TICKS_PER_EMIT, ticks))
        self.accum_y -= ticks * WHEEL_STEP_PX

        # Natural direct-touch direction:
        # finger up -> negative wheel -> reveal content below;
        # finger down -> positive wheel -> reveal older content above.
        wheel(ticks)
        self.total_ticks += abs(ticks)
        self.last_emit = now

    def watch(self, device: str) -> None:
        self.slot = 0
        self.slots.clear()
        self.finish()

        with open(device, "rb", buffering=0) as stream:
            try:
                self.x_min, self.x_max = read_abs_range(
                    stream.fileno(), ABS_MT_POSITION_X
                )
                self.y_min, self.y_max = read_abs_range(
                    stream.fileno(), ABS_MT_POSITION_Y
                )
            except OSError as exc:
                log(f"abs-range read failed: {exc}")

            log(
                f"watching {device} PASSIVELY (no exclusive grab), "
                f"x={self.x_min}..{self.x_max} y={self.y_min}..{self.y_max}"
            )

            while True:
                data = stream.read(EVENT.size)
                if len(data) != EVENT.size:
                    raise OSError("touchscreen event stream ended")

                _sec, _usec, ev_type, code, value = EVENT.unpack(data)
                if ev_type == EV_ABS:
                    if code == ABS_MT_SLOT:
                        self.slot = value
                    elif code == ABS_MT_TRACKING_ID:
                        state = self.state_for(self.slot)
                        if value < 0:
                            state["id"] = None
                            state["x"] = None
                            state["y"] = None
                        else:
                            state["id"] = value
                            state["x"] = None
                            state["y"] = None
                    elif code == ABS_MT_POSITION_X:
                        self.state_for(self.slot)["x"] = value
                    elif code == ABS_MT_POSITION_Y:
                        self.state_for(self.slot)["y"] = value
                elif ev_type == EV_SYN and code == SYN_REPORT:
                    self.report()

    def run(self) -> None:
        while True:
            device = find_device()
            if not device:
                log("Wacom touchscreen not found; retrying")
                time.sleep(1)
                continue
            try:
                self.watch(device)
            except (OSError, PermissionError) as exc:
                log(f"touchscreen reopen after error: {exc}")
                self.finish()
                time.sleep(1)


def main() -> None:
    desktop = DesktopState()
    desktop.poll_once()
    threading.Thread(
        target=desktop.loop,
        daemon=True,
        name="kitty-touch-desktop-state",
    ).start()
    KittyTouchScroll(desktop).run()


if __name__ == "__main__":
    main()
