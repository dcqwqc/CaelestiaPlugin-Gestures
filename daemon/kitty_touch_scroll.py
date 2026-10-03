#!/usr/bin/env python3
"""Passive one-finger touchscreen scrolling for Kitty on Hyprland.

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
from collections import deque
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

TOUCHSCREEN_NAME_OVERRIDE = os.environ.get("CAELESTIA_GESTURES_TOUCHSCREEN", "").strip().lower()
TOUCHSCREEN_OUTPUT_OVERRIDE = os.environ.get("CAELESTIA_GESTURES_TOUCHSCREEN_OUTPUT", "").strip()

# Direct-manipulation tuning in logical compositor pixels.
#
# Kitty is configured below to scroll exactly one terminal row per synthetic
# wheel notch. At 11 pt and cell_height 90%, one row is ~13.2 logical px:
# 11 pt * 96/72 px/pt * 0.90. Therefore one row of content moves for roughly
# one row of finger travel, keeping the touched text line visually anchored.
COMMIT_PX = 5.0
VERTICAL_DOMINANCE = 1.15
LINE_PX = 13.2
EMIT_INTERVAL = 0.010
MAX_TICKS_PER_EMIT = 4

# Kinetic scrolling uses the actual release velocity measured over the most
# recent touch samples. It starts at that speed, then loses speed smoothly.
VELOCITY_WINDOW = 0.110
VELOCITY_FRESHNESS = 0.060
MOMENTUM_MIN_SPEED = 90.0
MOMENTUM_MAX_SPEED = 3000.0
MOMENTUM_STOP_SPEED = 35.0
MOMENTUM_FRICTION = 3.8
MOMENTUM_FRAME = 1.0 / 60.0

STATE_POLL_INTERVAL = 0.18
MONITOR_POLL_INTERVAL = 2.0
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
        if TOUCHSCREEN_NAME_OVERRIDE:
            if TOUCHSCREEN_NAME_OVERRIDE in name:
                return dev
            continue
        try:
            proc = subprocess.run(
                ["udevadm", "info", "--query=property", "--name", dev],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, timeout=0.35, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "ID_INPUT_TOUCHSCREEN=1" in set((proc.stdout or "").splitlines()):
            return dev
    return None


def touchscreen_output(monitors: list[dict]) -> str | None:
    if TOUCHSCREEN_OUTPUT_OVERRIDE:
        return TOUCHSCREEN_OUTPUT_OVERRIDE
    devices = run_json("devices", "-j")
    if isinstance(devices, dict):
        for dev in devices.get("touch", []):
            output = str(dev.get("output") or "").strip()
            if output:
                return output
    for mon in monitors:
        name = str(mon.get("name") or "")
        if name.lower().startswith(("edp", "lvds", "dsi")):
            return name
    if monitors:
        focused = next((m for m in monitors if m.get("focused")), monitors[0])
        return str(focused.get("name") or "") or None
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
        self.last_monitor_poll = 0.0

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

        monitor = None
        now = time.monotonic()
        if now - self.last_monitor_poll >= MONITOR_POLL_INTERVAL:
            monitors = run_json("monitors", "-j")
            if isinstance(monitors, list):
                output = touchscreen_output(monitors)
                monitor = next((m for m in monitors if m.get("name") == output), None)
                if monitor is None and monitors:
                    monitor = next((m for m in monitors if m.get("focused")), monitors[0])
            self.last_monitor_poll = now

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
        self.motion_samples: deque[tuple[float, float]] = deque(maxlen=32)
        self.momentum_lock = threading.Lock()
        self.momentum_generation = 0

    def state_for(self, slot: int) -> dict[str, int | None]:
        return self.slots.setdefault(slot, {"id": None, "x": None, "y": None})

    def cancel_momentum(self) -> None:
        with self.momentum_lock:
            self.momentum_generation += 1

    def release_velocity(self) -> float:
        now = time.monotonic()
        if len(self.motion_samples) < 2:
            return 0.0

        samples = list(self.motion_samples)
        last_t, last_y = samples[-1]
        if now - last_t > VELOCITY_FRESHNESS:
            return 0.0

        cutoff = last_t - VELOCITY_WINDOW
        first_t, first_y = samples[0]
        for sample_t, sample_y in samples:
            if sample_t >= cutoff:
                first_t, first_y = sample_t, sample_y
                break

        dt = last_t - first_t
        if dt < 0.020:
            return 0.0

        velocity = (last_y - first_y) / dt
        return max(-MOMENTUM_MAX_SPEED, min(MOMENTUM_MAX_SPEED, velocity))

    def momentum_loop(self, generation: int, velocity: float) -> None:
        if abs(velocity) < MOMENTUM_MIN_SPEED:
            return

        log(f"momentum -> start velocity={velocity:.0f}px/s")
        accum = 0.0
        previous = time.monotonic()

        while abs(velocity) >= MOMENTUM_STOP_SPEED:
            with self.momentum_lock:
                if generation != self.momentum_generation:
                    log("momentum -> cancelled")
                    return

            window, _monitor = self.desktop.snapshot()
            if window is None:
                log("momentum -> stopped (Kitty focus lost)")
                return

            now = time.monotonic()
            dt = min(0.040, max(0.001, now - previous))
            previous = now

            # Integrate the current release velocity first so the first kinetic
            # frame continues at the exact speed the finger had at lift.
            accum += velocity * dt
            ticks = math.trunc(accum / LINE_PX)
            if ticks:
                ticks = max(-MAX_TICKS_PER_EMIT, min(MAX_TICKS_PER_EMIT, ticks))
                accum -= ticks * LINE_PX
                wheel(ticks)

            velocity *= math.exp(-MOMENTUM_FRICTION * dt)
            time.sleep(MOMENTUM_FRAME)

        log("momentum -> end")

    def start_momentum(self, velocity: float) -> None:
        if abs(velocity) < MOMENTUM_MIN_SPEED:
            return

        with self.momentum_lock:
            self.momentum_generation += 1
            generation = self.momentum_generation

        threading.Thread(
            target=self.momentum_loop,
            args=(generation, velocity),
            daemon=True,
            name="kitty-touch-momentum",
        ).start()

    def finish(self, momentum: bool = False) -> None:
        velocity = self.release_velocity() if self.committed and momentum else 0.0
        if self.committed:
            log(
                f"one-finger Kitty scroll -> end ticks={self.total_ticks} "
                f"release={velocity:.0f}px/s"
            )

        self.tracking = False
        self.committed = False
        self.started_inside = False
        self.accum_y = 0.0
        self.total_ticks = 0
        self.motion_samples.clear()

        if momentum:
            self.start_momentum(velocity)
        else:
            self.cancel_momentum()

    def report(self) -> None:
        active = [s for s in self.slots.values() if s["id"] is not None]
        positioned = [
            s for s in active if s["x"] is not None and s["y"] is not None
        ]

        # Only one-finger direct touch becomes terminal scrolling.
        # Two or more fingers remain entirely native and are ignored here.
        if len(active) != 1 or len(positioned) != 1:
            if self.tracking:
                self.finish(momentum=(len(active) == 0))
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
            self.cancel_momentum()
            self.tracking = True
            self.started_inside = point_inside_window(x, y, window)
            self.start_x = x
            self.start_y = y
            self.last_y = y
            self.accum_y = 0.0
            self.total_ticks = 0
            self.last_emit = 0.0
            self.motion_samples.clear()
            self.motion_samples.append((time.monotonic(), y))
            return

        if not self.started_inside:
            self.last_y = y
            return

        self.motion_samples.append((time.monotonic(), y))

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

        ticks = math.trunc(self.accum_y / LINE_PX)
        if not ticks:
            return

        ticks = max(-MAX_TICKS_PER_EMIT, min(MAX_TICKS_PER_EMIT, ticks))
        self.accum_y -= ticks * LINE_PX

        # Natural direct-touch direction:
        # finger up -> negative wheel -> reveal content below;
        # finger down -> positive wheel -> reveal older content above.
        wheel(ticks)
        self.total_ticks += abs(ticks)
        self.last_emit = now

    def watch(self, device: str) -> None:
        self.slot = 0
        self.slots.clear()
        self.finish(momentum=False)

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
