#!/usr/bin/env python3
"""Persist the Hyprgrass four-finger touchscreen OSK swipe bindings.

Avoid restarting any session. This config is read by Hyprland on reload and
on next login; it uses the same Yoga keyboard as the touchpad evdev daemon.
"""
from datetime import datetime
from pathlib import Path
import shutil

config = Path.home() / ".config/caelestia/hypr-user.lua"
marker = "-- Caelestia Gestures: direct-touch four-finger OSK"
block = """
-- Caelestia Gestures: direct-touch four-finger OSK
-- Hyprgrass receives Wayland touchscreen gestures directly. Touchpad gestures
-- are owned separately by the Gestures evdev daemon. Keep only one OSK toggle
-- transport per input surface to avoid double toggles.
for _, direction in ipairs({ "left", "right", "up", "down" }) do
    hl.plugin.hyprgrass.bind({
        pattern = { kind = "swipe", fingers = 4, direction = direction },
        action = hl.dsp.exec_cmd(yoga .. " osk toggle"),
    })
end
"""
text = config.read_text()
if marker in text:
    print("Touchscreen Hyprgrass four-finger bindings already installed")
else:
    anchor = "-- Workspace overview (hyprtasking)"
    if text.count(anchor) != 1:
        raise SystemExit("Cannot safely locate Hyprgrass bindings in hypr-user.lua")
    backup = config.with_name(config.name + ".bak-osk-touchscreen-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(config, backup)
    config.write_text(text.replace(anchor, block + chr(10) + anchor, 1))
    print(f"Installed Hyprgrass four-finger touchscreen bindings; backup: {backup}")
