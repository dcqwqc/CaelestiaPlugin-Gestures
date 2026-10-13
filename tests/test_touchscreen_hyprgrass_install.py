"""Hyprgrass integration has stable four-way mapping and idempotent setup."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts' / 'integrate-touchscreen-osk.py'


class TouchscreenHyprgrassInstallTest(unittest.TestCase):
    def test_four_way_bindings_are_persistent_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / '.config/caelestia/hypr-user.lua'
            config.parent.mkdir(parents=True)
            config.write_text('local yoga = "/tmp/yoga-tablet"\n-- Workspace overview (hyprtasking)\n')
            env = dict(os.environ, HOME=td)
            first = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
            second = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
            data = config.read_text()
            self.assertIn('backup:', first.stdout)
            self.assertIn('already installed', second.stdout)
            self.assertEqual(data.count('Caelestia Gestures: direct-touch four-finger OSK'), 1)
            self.assertIn('ipairs({ "left", "right", "up", "down" })', data)
            self.assertIn('kind = "swipe", fingers = 4', data)
            self.assertIn('yoga .. " osk toggle"', data)
            self.assertEqual(len(list(config.parent.glob('hypr-user.lua.bak-osk-touchscreen-*'))), 1)

    def test_touchscreen_raw_watcher_not_also_calling_keyboard(self):
        code = (ROOT/'daemon/mirai_gestures.py').read_text()
        self.assertIn('self.kind == "touchpad" and len(active) == 4', code)
        self.assertIn('yoga-tablet osk toggle', code)


if __name__ == '__main__':
    unittest.main()
