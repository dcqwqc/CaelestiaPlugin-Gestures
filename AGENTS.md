# Codex notes

This project owns Mirai's low-level multi-touch gesture policy.

Non-negotiable invariants:
- Never EVIOCGRAB the touchpad or touchscreen.
- Preserve native two-finger scrolling, pointer motion, touchscreen input, and Wacom pen input.
- Preserve the existing four-finger touchpad swipe that toggles Kagami Remote.
- Aseprite pinch-to-wheel translation must be scoped to an active Aseprite window only.
- Mirai disables native touchpad tap-to-click so a 3-finger tap cannot also emit MMB. Therefore this daemon must preserve 1-finger left click, 2-finger right click, and tap-drag behavior.
- Do not duplicate the four-finger remote gesture in another daemon.
