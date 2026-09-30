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


## Aseprite direct-touch mode

On Mirai, Aseprite 1.3.x runs through XWayland and does not expose the native
finger gesture layer available on Windows/macOS. While Aseprite is focused the
service temporarily disables only Hyprland's `wacom-hid-53b7-finger` device and
reads that kernel event stream directly:

- one finger drag -> middle-button canvas pan
- two-finger pinch -> wheel zoom
- three-finger tap -> Ctrl+Z
- four-finger tap -> Ctrl+Y

The Wacom pen/stylus is a separate device and is never disabled. When focus
leaves Aseprite, native Hyprland touchscreen handling is restored automatically.
