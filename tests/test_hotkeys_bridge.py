import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

source=Path(__file__).resolve().parents[1]/"daemon/mirai_gestures.py"
spec=importlib.util.spec_from_file_location("gesture_bridge_test",source)
gestures=importlib.util.module_from_spec(spec)
spec.loader.exec_module(gestures)

class HotkeysBridgeTests(unittest.TestCase):
    def test_reserved_basic_taps_not_forwarded(self):
        self.assertFalse(gestures.hotkeys_tap("touchpad",2))
        self.assertFalse(gestures.hotkeys_tap("touchscreen",1))
        self.assertFalse(gestures.hotkeys_tap("vr_gesture",3))

    def test_service_absence_keeps_undo_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ,{"XDG_RUNTIME_DIR":folder}):
                self.assertFalse(gestures.hotkeys_tap("touchpad",3))

    def test_valid_custom_action_is_consumed(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"celestios-control.sock"
            ready=threading.Event()
            seen={}
            def server():
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                    sock.bind(str(path));sock.listen(1);ready.set()
                    conn,_=sock.accept()
                    with conn:
                        raw=conn.recv(4096)
                        seen.update(json.loads(raw))
                        conn.sendall(json.dumps({"ok":True,"handled":True}).encode()+bytes([10]))
            th=threading.Thread(target=server,daemon=True);th.start()
            self.assertTrue(ready.wait(2))
            with patch.dict(os.environ,{"XDG_RUNTIME_DIR":folder}):
                self.assertTrue(gestures.hotkeys_tap("touchpad",3))
            th.join(2)
            self.assertEqual(seen["trigger"],"tap3")
            self.assertEqual(seen["event"],"tap")

    def test_unmapped_tap_does_not_override_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"celestios-control.sock"
            ready=threading.Event()
            def server():
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                    sock.bind(str(path));sock.listen(1);ready.set()
                    conn,_=sock.accept()
                    with conn:
                        conn.recv(4096)
                        conn.sendall(json.dumps({"ok":True,"handled":False}).encode()+bytes([10]))
            th=threading.Thread(target=server,daemon=True);th.start()
            self.assertTrue(ready.wait(2))
            with patch.dict(os.environ,{"XDG_RUNTIME_DIR":folder}):
                self.assertFalse(gestures.hotkeys_tap("touchscreen",4))
            th.join(2)

if __name__=="__main__":
    unittest.main()
