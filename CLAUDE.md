# Claude Code context

Mirai Gestures is the single owner of Mirai's raw touchpad gesture bridge.

Keep these behaviors intact: Aseprite 2-finger pinch zoom on touchpad + touchscreen, 3-finger tap Ctrl+Z, 4-finger tap Ctrl+Y, native scrolling/pointer/pen behavior, and the legacy 4-finger Remote Desktop swipe. Native touchpad tap-to-click is intentionally disabled on Mirai because libinput would otherwise emit a middle click for the 3-finger Undo gesture; this daemon re-emits 1/2-finger taps and tap-drag itself.
