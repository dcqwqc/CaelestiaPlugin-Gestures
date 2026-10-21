# Native Caelestia Plugin API migration

Experimental target: https://github.com/caelestia-dots/shell/pull/1703 (unmerged). This branch does not alter the original default branch.

- Plugin identity: `dcqwqc/Gestures`; minimum upstream development shell: >=2.1.0.
- Entry-point types: custom, quick-toggle.
- Settings: Settings.qml; settings UI: none.
- Upstream parser supports custom, bar-entry, bar-popout, quick-toggle, status-icon, dashboard-tab; actual integrated placements currently include bar and quick toggles. Existing custom and `properties.slot` placements require the existing private hook or a new adapter. Verify rather than assume they render.

## Blockers before deployment
1. Compile/launch with an isolated checkout of upstream PR #1703; verify manifest loading, QML imports, all source paths and plugin enable/disable/reload.
2. Verify native settings persistence and user-visible placement; do not discard previous settings/data.
3. Exercise hardware/backend behavior, multi-monitor/Wayland integration and rollback.
4. Keep the original installed plugin and default branch available. Deploy only after a working live test and rollback mechanism.

**Status:** Manifest development version gate applied; not deployed or runtime verified.

## Production-safe merge profile

The root `manifest.json` retains the deployed Caelestia fork entry points and has **no upstream-only minimum version requirement**. The experimental official API profile is in `manifest.native-dev.json`; it is not used by the current fork loader. Do not replace the root file with the development profile until the upstream host implements all required custom panels and placement hooks.
