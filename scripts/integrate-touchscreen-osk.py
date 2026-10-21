#!/usr/bin/env python3
"""Install Mirai's touchscreen system gestures into the active Hyprland Lua config.

This deliberately does NOT enable a second evdev daemon: Caelestia starts the
existing gesture service. Hyprgrass handles Wayland touchscreen gestures;
the existing evdev reader owns the touchpad's four-finger OSK toggle.
"""
from datetime import datetime
from pathlib import Path
import shutil

CONFIG = Path.home() / ".config/caelestia/hypr-user.lua"
START = "-- Caelestia Gestures: direct-touch system gestures"
END = "-- Caelestia Gestures: end direct-touch system gestures"
OLD = "-- Caelestia Gestures: direct-touch four-finger OSK"
ANCHOR = "-- Workspace overview (hyprtasking)"
BLOCK = r'''
-- Caelestia Gestures: direct-touch system gestures
-- Hyprgrass owns the direct screen; evdev handles touchpad four-finger OSK.
-- Native Wayland gesture routing is required to cancel app pinch/zoom.
local gesturePlugin = os.getenv("HOME") .. "/.local/lib/hyprland/libhyprgrass-mirai.so"
local gestureYoga = os.getenv("HOME") .. "/.local/bin/yoga-tablet"
hl.plugin.load(gesturePlugin)
hl.config({plugin = {hyprgrass = {
    sensitivity = 3.0,
    long_press_delay = 400,
    resize_on_border_long_press = true,
}}})
for _, direction in ipairs({"left", "right", "up", "down"}) do
    hl.plugin.hyprgrass.bind({
        pattern = {kind = "swipe", fingers = 4, direction = direction},
        action = hl.dsp.exec_cmd(gestureYoga .. " osk toggle"),
    })
end
-- A three-finger hold starts a compositor-native window grab; this is
-- independent from the short down-swipe used to close the selected window.
hl.plugin.hyprgrass.bind({
    pattern = {kind = "longpress", fingers = 3},
    action = hl.dsp.window.drag(),
    mouse = true,
})
hl.plugin.hyprgrass.gesture({
    pattern = {kind = "swipe", fingers = 3, direction = "down"},
    action = "close",
})
-- Caelestia Gestures: end direct-touch system gestures
'''.strip()


def install(config: Path = CONFIG) -> bool:
    text = config.read_text()
    if START in text:
        if END not in text:
            raise RuntimeError("Partial gesture block: refusing unsafe replacement")
        before, _, remaining = text.partition(START)
        _, _, after = remaining.partition(END)
        replacement = before + BLOCK + after
    elif OLD in text:
        raise RuntimeError("Legacy gesture block detected; migrate manually")
    else:
        if text.count(ANCHOR) != 1:
            raise RuntimeError("Cannot safely locate unique gesture config anchor")
        replacement = text.replace(ANCHOR, BLOCK + "\n\n" + ANCHOR, 1)
    if replacement == text:
        return False
    backup = config.with_name(config.name + ".bak-system-gestures-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(config, backup)
    config.write_text(replacement)
    print(f"Installed direct-touch gestures; backup: {backup}")
    return True


if __name__ == "__main__":
    if not install():
        print("Direct-touch gestures already match desired configuration")
