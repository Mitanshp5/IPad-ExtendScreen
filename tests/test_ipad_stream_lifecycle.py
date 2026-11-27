"""Source-wiring checks for UIKit lifecycle callbacks (not a device test)."""

import re
import unittest
from pathlib import Path


SOURCE = (Path(__file__).resolve().parents[1] / "screen" / "Sources" /
          "PSScreenViewController.m").read_text(encoding="utf-8")


def body(selector):
    method = re.search(rf"^- \(void\){re.escape(selector)}[^{{\n]*\{{\n(.*?)^\}}", SOURCE, re.M | re.S)
    if method is None:
        raise AssertionError(f"Missing method: {selector}")
    return method.group(1)


class IPadStreamLifecycleTests(unittest.TestCase):
    def assertNoTeardown(self, method):
        self.assertNotIn("[self stopEverything]", body(method))

    def test_opening_in_app_screens_does_not_stop_session(self):
        for method in ("openSettings", "openQRScannerDirect", "viewWillDisappear:"):
            with self.subTest(method=method):
                self.assertNoTeardown(method)

    def test_inactive_and_background_are_not_session_end(self):
        for event in ("UIApplicationWillResignActiveNotification", "UIApplicationDidEnterBackgroundNotification"):
            with self.subTest(event=event):
                handler = re.search(rf"@selector\(([^)]+)\) name:{event}\b", SOURCE)
                if handler:
                    self.assertNoTeardown(handler.group(1))

    def test_app_termination_still_stops_the_session(self):
        handler = re.search(r"@selector\(([^)]+)\) name:UIApplicationWillTerminateNotification\b", SOURCE)
        if handler is None:
            self.fail("Missing termination observer")
        self.assertIn("[self stopEverything]", body(handler.group(1)))
        for action in ("[self.stream stop]", "[self.cableServer stop]"):
            self.assertIn(action, body("stopEverything"))


if __name__ == "__main__":
    unittest.main()
