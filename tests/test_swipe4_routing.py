"""Synthetic raw-contact regression tests for four-finger keyboard toggling."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "daemon/mirai_gestures.py"
SPEC = importlib.util.spec_from_file_location("gesture_swipe4_test", SOURCE)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class FourFingerRoutingTests(unittest.TestCase):
    def make_watcher(self, kind):
        watcher = M.MTWatcher(kind)
        watcher.abs_x_min = watcher.abs_y_min = 0
        watcher.abs_x_max = watcher.abs_y_max = 1000
        watcher.slots = {
            i: {"id": i + 1, "x": 400, "y": 400}
            for i in range(4)
        }
        return watcher

    def move(self, watcher, x=0, y=0):
        for slot in watcher.slots.values():
            slot["x"] = 400 + x
            slot["y"] = 400 + y
        watcher.report()

    def test_eight_directions_on_touchpad_and_direct_screen(self):
        directions = [
            (150, 0), (-150, 0), (0, 150), (0, -150),
            (120, 120), (-120, 120), (120, -120), (-120, -120),
        ]
        for kind in ("touchpad", "touchscreen"):
            for dx, dy in directions:
                with self.subTest(kind=kind, direction=(dx, dy)):
                    watcher = self.make_watcher(kind)
                    with (patch.object(M, "quiet_popen") as run,
                          patch.object(M, "keyboard_guard_active", return_value=False),
                          patch.object(M, "aseprite_active", return_value=False),
                          patch.object(M, "touchscreen_logical_geometry", return_value=(0, 0, 1440, 900, 0))):
                        self.move(watcher)
                        self.move(watcher, x=dx, y=dy)
                        self.move(watcher, x=dx, y=dy)
                        run.assert_called_once_with([M.YOGA_TABLET, "osk", "toggle"])

    def test_can_toggle_again_after_all_fingers_lift(self):
        watcher = self.make_watcher("touchscreen")
        with (patch.object(M, "quiet_popen") as run,
              patch.object(M, "aseprite_active", return_value=False),
              patch.object(M, "touchscreen_logical_geometry", return_value=(0, 0, 1440, 900, 0))):
            self.move(watcher)
            self.move(watcher, x=150)
            watcher.slots.clear()
            watcher.report()
            watcher.slots = {
                i: {"id": i + 10, "x": 400, "y": 400}
                for i in range(4)
            }
            self.move(watcher)
            self.move(watcher, y=-150)
            self.assertEqual(run.call_count, 2)
            for call in run.call_args_list:
                self.assertEqual(call.args[0], [M.YOGA_TABLET, "osk", "toggle"])

    def test_small_movement_does_not_trigger(self):
        for kind in ("touchpad", "touchscreen"):
            with self.subTest(kind=kind):
                watcher = self.make_watcher(kind)
                with (patch.object(M, "quiet_popen") as run,
                      patch.object(M, "keyboard_guard_active", return_value=False),
                      patch.object(M, "aseprite_active", return_value=False),
                      patch.object(M, "touchscreen_logical_geometry", return_value=(0,0,1440,900,0)),
                      patch.object(M, "hypr_shortcut") as shortcut,
                      patch.object(M, "hotkeys_tap", return_value=False)):
                    self.move(watcher)
                    self.move(watcher, x=20, y=20)
                    watcher.slots.clear()
                    watcher.report()
                    run.assert_not_called()
                    shortcut.assert_called_once_with("y")

    def test_short_swipe_never_misfires_four_finger_tap(self):
        for kind in ("touchpad", "touchscreen"):
            with self.subTest(kind=kind):
                watcher = self.make_watcher(kind)
                with (patch.object(M, "quiet_popen") as run,
                      patch.object(M, "keyboard_guard_active", return_value=False),
                      patch.object(M, "aseprite_active", return_value=False),
                      patch.object(M, "touchscreen_logical_geometry", return_value=(0,0,1440,900,0)),
                      patch.object(M, "hotkeys_tap") as hotkeys,
                      patch.object(M, "hypr_shortcut") as shortcut):
                    self.move(watcher)
                    self.move(watcher, y=70)
                    watcher.slots.clear()
                    watcher.report()
                    run.assert_not_called()
                    hotkeys.assert_not_called()
                    shortcut.assert_not_called()


if __name__ == "__main__":
    unittest.main()
