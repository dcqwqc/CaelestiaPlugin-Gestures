# Caelestia Gestures / Mirai Gestures

Mirai-specific touchpad and direct-touch gesture integration for Hyprland/Caelestia.

- Two-finger pinch in **Aseprite** -> smooth canvas zoom (touchpad + touchscreen)
- Three-finger tap -> `Ctrl+Z` (Undo)
- Four-finger tap -> `Ctrl+Y` (Redo)
- Existing four-finger touchpad swipe -> Kagami Remote toggle
- One-finger tap, two-finger right-click and tap-drag are preserved after native touchpad tap-to-click is disabled to prevent the 3-finger middle-click collision.

The daemon reads the physical multitouch event streams without grabbing them, so two-finger scrolling, cursor movement, touchscreen interaction, and pen input stay native.

## Mirai installation

`install.sh` deploys the daemon/service, disables the superseded four-finger-only service, and symlinks the Caelestia plugin into the plugin directory.
