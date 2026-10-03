# Caelestia Gestures

Hardware-discovered touchpad and touchscreen gesture integration for Hyprland and Caelestia.

The daemon discovers touchpads and touchscreens through udev capability tags rather than laptop-specific model names. The touchscreen output is taken from Hyprland when available and otherwise falls back to the internal panel or focused monitor.

Optional environment overrides are available for unusual hardware:

- CAELESTIA_GESTURES_TOUCHPAD
- CAELESTIA_GESTURES_TOUCHPAD_HYPR
- CAELESTIA_GESTURES_TOUCHSCREEN
- CAELESTIA_GESTURES_TOUCHSCREEN_OUTPUT

Features include app-aware multi-touch handling, Aseprite pinch/pan support, terminal direct-touch scrolling with momentum, gesture Undo/Redo, and optional Remote Desktop swipe integration.

Legacy filenames such as mirai_gestures.py and mirai-gestures.service are retained only as compatibility entry points for existing installations; runtime device discovery is hardware-agnostic.
