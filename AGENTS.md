# Codex notes

This project owns Mirai's low-level multi-touch gesture policy.

Non-negotiable invariants:
- Never EVIOCGRAB the touchpad or touchscreen.
- Preserve native two-finger scrolling, pointer motion, touchscreen input, and Wacom pen input.
- Preserve the existing four-finger touchpad swipe that toggles Kagami Remote.
- Aseprite pinch-to-wheel translation must be scoped to an active Aseprite window only.
- Mirai disables native touchpad tap-to-click so a 3-finger tap cannot also emit MMB. Therefore this daemon must preserve 1-finger left click, 2-finger right click, and tap-drag behavior.
- Do not duplicate the four-finger remote gesture in another daemon.

Broader project inventory: ../PROJECTLIST.md and sumi://page/3fe801ca-0a19-43ed-ba9d-123479666d88.


## Fast visible delivery

- **Fast visible delivery:** When the user explicitly requests a familiar, repeatable GUI result on their screen, display it directly on their current workspace instead of re-staging it in the AI workspace. Deliver first; then, without changing focus or otherwise interrupting the user, quietly capture a screenshot to verify it. Use the private AI workspace for new, uncertain, or exploratory GUI work.

## Verified GUI handoff

- **Verified GUI handoff:** For GUI work that is new, uncertain, or exploratory, use the private AI workspace and only run `ai-workspace handoff <Hyprland selector>` for its final window after all requested work is complete and verified and when the user explicitly asks to see it or the request unmistakably calls for it. It moves the selected final window to the user’s current workspace, raises it, and displays it fullscreen. `ai-workspace move` only moves a window into the test workspace. Never hand off an in-progress or unverified result.
