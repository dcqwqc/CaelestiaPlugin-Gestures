"""Guard touchscreen gesture configuration against regressions and duplicate owners."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "integrate-touchscreen-osk.py"


class TouchscreenHyprgrassInstallTest(unittest.TestCase):
    def test_idempotent_binds_and_backup(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".config/caelestia/hypr-user.lua"
            config.parent.mkdir(parents=True)
            config.write_text("-- Workspace overview (hyprtasking)\n")
            env = dict(os.environ, HOME=td)
            first = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
            second = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
            data = config.read_text()
            self.assertIn("backup:", first.stdout)
            self.assertIn("already match", second.stdout)
            self.assertEqual(data.count("-- Caelestia Gestures: direct-touch system gestures"), 1)
            self.assertEqual(data.count("hl.plugin.load(gesturePlugin)"), 1)
            self.assertIn('{"left", "right", "up", "down"}', data)
            self.assertIn('fingers = 4, direction = direction', data)
            self.assertIn('gestureYoga .. " osk toggle"', data)
            self.assertIn('kind = "longpress", fingers = 3', data)
            self.assertIn('action = hl.dsp.window.drag()', data)
            self.assertIn('kind = "swipe", fingers = 3, direction = "down"', data)
            self.assertIn('action = "close"', data)
            self.assertEqual(len(list(config.parent.glob("hypr-user.lua.bak-system-gestures-*"))), 1)

    def test_touchscreen_raw_watcher_never_double_toggles(self):
        source = (ROOT / "daemon/mirai_gestures.py").read_text()
        self.assertIn('self.kind == "touchpad" and len(active) == 4', source)

    def test_refuses_malformed_existing_block(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / ".config/caelestia/hypr-user.lua"
            config.parent.mkdir(parents=True)
            original = "-- Caelestia Gestures: direct-touch system gestures\n"
            config.write_text(original)
            env = dict(os.environ, HOME=td)
            result = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(), original)


if __name__ == "__main__":
    unittest.main()
