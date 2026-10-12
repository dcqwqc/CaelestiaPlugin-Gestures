"""Four-finger axis routing regression tests; no real input device required."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'daemon/mirai_gestures.py'
SPEC = importlib.util.spec_from_file_location('gesture_swipe4_test', SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FourFingerRoutingTests(unittest.TestCase):
    def make_watcher(self, kind):
        watcher = M.MTWatcher(kind)
        watcher.abs_x_min = watcher.abs_y_min = 0
        watcher.abs_x_max = watcher.abs_y_max = 1000
        watcher.slots = {i: {'id': i + 1, 'x': 400, 'y': 400} for i in range(4)}
        return watcher

    def move(self, watcher, x=0, y=0):
        for slot in watcher.slots.values():
            slot['x'] = 400 + x
            slot['y'] = 400 + y
        watcher.report()

    def test_horizontal_both_devices_and_once_only(self):
        for kind in ('touchpad', 'touchscreen'):
            for dx, action in ((150, 'show'), (-150, 'hide')):
                with self.subTest(kind=kind, action=action):
                    watcher = self.make_watcher(kind)
                    with (patch.object(M, 'quiet_popen') as run,
                          patch.object(M, 'keyboard_guard_active', return_value=False),
                          patch.object(M, 'aseprite_active', return_value=False),
                          patch.object(M, 'touchscreen_logical_geometry', return_value=(0,0,1440,900,0)),
                          patch.object(M, 'toggle_remote') as remote):
                        self.move(watcher)
                        self.move(watcher, x=dx // 3, y=4)
                        self.move(watcher, x=dx, y=12)
                        self.move(watcher, x=dx + (10 if dx > 0 else -10))
                        run.assert_called_once_with([M.YOGA_TABLET, 'osk', action])
                        remote.assert_not_called()

    def test_vertical_swipes_reserved_for_workspace_grid(self):
        for kind in ('touchpad', 'touchscreen'):
            for dy in (180, -180):
                with self.subTest(kind=kind, dy=dy):
                    watcher = self.make_watcher(kind)
                    with (patch.object(M, 'quiet_popen') as run,
                          patch.object(M, 'keyboard_guard_active', return_value=False),
                          patch.object(M, 'aseprite_active', return_value=False),
                          patch.object(M, 'touchscreen_logical_geometry', return_value=(0,0,1440,900,0)),
                          patch.object(M, 'toggle_remote') as remote,
                          patch.object(M, 'hotkeys_tap') as hotkeys,
                          patch.object(M, 'hypr_shortcut') as shortcut):
                        self.move(watcher)
                        self.move(watcher, x=4, y=dy//3)
                        self.move(watcher, x=150, y=dy)
                        watcher.slots.clear()
                        watcher.report()
                        run.assert_not_called()
                        remote.assert_not_called()
                        hotkeys.assert_not_called()
                        shortcut.assert_not_called()

    def test_short_vertical_and_diagonal_are_not_taps(self):
        for x,y in ((0, 80), (80, 0), (60, 60)):
            watcher = self.make_watcher('touchpad')
            with (patch.object(M, 'quiet_popen') as osk,
                  patch.object(M, 'keyboard_guard_active', return_value=False),
                  patch.object(M, 'hotkeys_tap') as hotkeys,
                  patch.object(M, 'hypr_shortcut') as shortcut):
                self.move(watcher)
                self.move(watcher, x=x, y=y)
                watcher.slots.clear()
                watcher.report()
                osk.assert_not_called()
                hotkeys.assert_not_called()
                shortcut.assert_not_called()

    def test_diagonal_and_tiny_motion_ignored(self):
        w = self.make_watcher('touchpad')
        with (patch.object(M, 'quiet_popen') as run,
              patch.object(M, 'keyboard_guard_active', return_value=False)):
            self.move(w)
            self.move(w, x=20, y=20)
            self.move(w, x=60, y=60)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
