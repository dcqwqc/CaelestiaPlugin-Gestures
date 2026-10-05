# Claude Code context

Mirai Gestures is the single owner of Mirai's raw touchpad gesture bridge.

Keep these behaviors intact: Aseprite 2-finger pinch zoom on touchpad + touchscreen, 3-finger tap Ctrl+Z, 4-finger tap Ctrl+Y, native scrolling/pointer/pen behavior, and the legacy 4-finger Remote Desktop swipe. Native touchpad tap-to-click is intentionally disabled on Mirai because libinput would otherwise emit a middle click for the 3-finger Undo gesture; this daemon re-emits 1/2-finger taps and tap-drag itself.

Broader project inventory: ../PROJECTLIST.md and sumi://page/3fe801ca-0a19-43ed-ba9d-123479666d88.


## Fast visible delivery

- **Fast visible delivery:** When the user explicitly requests a familiar, repeatable GUI result on their screen, display it directly on their current workspace instead of re-staging it in the AI workspace. Deliver first; then, without changing focus or otherwise interrupting the user, quietly capture a screenshot to verify it. Use the private AI workspace for new, uncertain, or exploratory GUI work.

## Verified GUI handoff

- **Verified GUI handoff:** For GUI work that is new, uncertain, or exploratory, use the private AI workspace and only run `ai-workspace handoff <Hyprland selector>` for its final window after all requested work is complete and verified and when the user explicitly asks to see it or the request unmistakably calls for it. It moves the selected final window to the user’s current workspace, raises it, and displays it fullscreen. `ai-workspace move` only moves a window into the test workspace. Never hand off an in-progress or unverified result.
