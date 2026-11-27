import atexit
import json
import runpy
import signal
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class TrayHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import without installing the app's display-reset handlers.
        with (
            patch.object(atexit, "register", side_effect=lambda callback: callback),
            patch.object(signal, "signal"),
            patch.object(threading.Thread, "_bootstrap", getattr(threading.Thread, "_bootstrap")),
        ):
            cls.tray = runpy.run_path(str(ROOT / "host-tools" / "tray-host.py"))
        cls.globals = cls.tray["load_tray_settings"].__globals__

    def setUp(self):
        temp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = temp / "settings.json"
        self.enterContext(patch.dict(
            self.globals, SETTINGS_FILE=self.settings, LOG_FILE=temp / "tray.log",
            SESSION_FILE=temp / "session.json", setup_process_job=lambda: None,
        ))

    def test_host_starts_at_60_fps_in_both_saved_modes(self):
        for mode, old_fps in (("cable", 45), ("wifi", 30)):
            with self.subTest(mode=mode):
                self.settings.write_text(json.dumps({
                    "mode": mode, "fps": old_fps, "cable_fps": 45, "wifi_fps": 30,
                }), encoding="utf-8")
                manager = self.tray["ExtendServiceManager"]()
                with patch.object(manager, "kill_all"), patch("subprocess.Popen") as popen, patch("threading.Thread"):
                    self.assertTrue(manager.start())
                command = popen.call_args.args[0]
                self.assertEqual(command[command.index("--fps") + 1], "60")
                self.assertEqual("--cable" in command, mode == "cable")
                self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8"))["mode"], mode)

    def test_popup_has_no_close_button_or_fps_picker_in_either_mode(self):
        class StoppedService:
            pair_code = url = None

            def is_running(self):
                return False

        def visible_labels(widget):
            labels = {widget.cget("text")} if isinstance(widget, tk.Label) else set()
            for child in widget.pack_slaves():
                labels.update(visible_labels(child))
            return labels

        root = tk.Tk()
        self.addCleanup(root.destroy)
        root.withdraw()
        with patch.dict(self.globals, detect_lan_ip=lambda: "127.0.0.1"):
            window = self.tray["ControlWindow"](root, StoppedService())
            for mode in ("cable", "wifi"):
                with self.subTest(mode=mode):
                    window._select_mode(mode)
                    labels = visible_labels(window)
                    self.assertFalse(labels & {"✕", "Framerate:"})
                    self.assertIn("Hide to Tray", labels)
                    self.assertIn("Exit Application", labels)
                    self.assertEqual("📶 WI-FI PAIRING QR CODE" in labels, mode == "wifi")
                    window.update_state(True)
                    self.assertIn("Up to 60 FPS", window.lbl_substatus.cget("text"))


if __name__ == "__main__":
    unittest.main()
