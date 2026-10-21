# Caelestia Gestures

Hardware-discovered touchpad and touchscreen gesture integration for Hyprland and Caelestia.

The daemon discovers touchpads and touchscreens through udev capability tags rather than laptop-specific model names. The touchscreen output is taken from Hyprland when available and otherwise falls back to the internal panel or focused monitor.

Optional environment overrides are available for unusual hardware:

- CAELESTIA_GESTURES_TOUCHPAD
- CAELESTIA_GESTURES_TOUCHPAD_HYPR
- CAELESTIA_GESTURES_TOUCHSCREEN
- CAELESTIA_GESTURES_TOUCHSCREEN_OUTPUT

Features include app-aware multi-touch handling, Aseprite pinch/pan support, terminal direct-touch scrolling with momentum, gesture Undo/Redo, optional Remote Desktop swipe integration, and a settings-controlled double-click-to-fullscreen toggle.

Legacy filenames such as mirai_gestures.py and mirai-gestures.service are retained only as compatibility entry points for existing installations; runtime device discovery is hardware-agnostic.


## Double-click fullscreen

The plugin settings include **Double-click to fullscreen**. When enabled, two primary clicks on the same window within 380 ms and 32 px toggle Hyprland fullscreen. The same gesture exits fullscreen. Physical mouse/clickpad buttons and the plugin's synthetic one-finger touchpad taps are supported; disabling the setting stops the detector without disabling the rest of the gesture daemon.

## Shared Hotkeys integration

The low-level daemon now optionally publishes completed three- and four-finger
touchpad and touchscreen taps to the local Hotkeys UNIX socket. A user mapping
with input source touchpad/touchscreen and token tap3/tap4 takes precedence;
otherwise the original Ctrl+Z Undo / Ctrl+Y Redo behavior is preserved. The
live 15-second capture window can record a tap without executing Undo/Redo.

No physical device grabs or read policies were added, and one/two-finger taps,
native scrolling, remote-desktop swipes and Aseprite pan/zoom remain unchanged.
See Hotkeys provider specification in dcqwqc/CaelestiaPlugin-Hotkeys.


## Four-finger touch keyboard toggle

A four-finger swipe in any direction toggles the existing Yoga OSK. On the
touchpad, raw evdev handling continues to work independently. On the direct
touchscreen, **Hyprgrass** recognizes swipes in each of the four directions;
the low-level watcher does not also toggle the OSK, preventing double actions.
Run `scripts/integrate-touchscreen-osk.py` or `install.sh` to persist the
four Hyprgrass bindings in `~/.config/caelestia/hypr-user.lua`.
Hyprtasking's four-finger trigger remains disabled (Super+Tab opens the grid).


## Three-finger window control (Hyprland 0.56)

The active Mirai Hyprland Lua config uses Hyprgrass for direct touchscreen
three-finger long-press + drag to move a window, and a short three-finger
downward swipe to close the touched window. The touchpad uses native Hyprland
three-finger directional gestures: down closes; up/left/right move a window.

The integration script registers these together with the four-direction direct
screen OSK gestures and loads the matching pinned Hyprgrass binary. It is
idempotent and creates a backup before editing the Hyprland config. The script
must be rerun after restoring an older hypr-user.lua. Hyprgrass must be rebuilt
for the new ABI after a Hyprland upgrade.

When Caelestia QuickShell manages the plugin, **do not enable** the legacy
mirai-gestures.service as a second process. The installer enforces this.
The Zen touchscreen swipe bridge independently captures the touchscreen while
Zen is focused; its classifier must defer early two-finger decisions to give
three/four-finger system gestures priority.
