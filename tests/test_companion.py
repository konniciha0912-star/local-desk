"""Native view ownership and truthful activity, without an OS desktop."""

import threading
import time
import unittest
from unittest.mock import Mock
from companion import Companion, activity, position


class CompanionTests(unittest.TestCase):
    def controller(self):
        main, bar, session, document = Mock(), Mock(), Mock(), Mock()
        main.destroy.side_effect = lambda: control.closing(main)
        bar.destroy.side_effect = lambda: control.closing(bar)
        geometry = {"x": -1700, "y": 40, "width": 1000, "height": 700, "presentation": "normal"}
        control = Companion(
            main, bar, session, document, geometry, [Mock(x=-1920, y=0, width=1920, height=1080)]
        )
        return control

    def test_expand_normal_preserves_main_geometry_on_negative_monitor(self):
        control = self.controller()
        original = dict(control.geometry)
        control.action("expand")
        control.bar.move.assert_called_with(-1190, 12)
        control.bar.resize.assert_called_with(460, 460)
        control.main.hide.assert_called_once()
        self.assertTrue(control.expanded)
        control.action("collapse")
        control.bar.resize.assert_called_with(320, 68)
        control.action("normal")
        self.assertEqual(control.geometry, original)
        control.main.show.assert_called_once()
        control.bar.hide.assert_called_once()

    def test_either_window_close_stops_owner_and_destroys_other_once(self):
        for source in ("main", "bar"):
            with self.subTest(source=source):
                c = self.controller()
                c.closing(getattr(c, source))
                self.assertTrue(c.closed)
                c.session.request_stop.assert_called_once()
                deadline = time.monotonic() + 1
                while (
                    not getattr(c, "bar" if source == "main" else "main").destroy.called
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.001)
                getattr(c, "bar" if source == "main" else "main").destroy.assert_called_once()
                with self.assertRaises(ValueError):
                    c.action("expand")

    def test_startup_failure_restores_recovery_view(self):
        c = self.controller()
        c.mode = "companion"
        c.session.server.application.startup_error = "fixture"
        c.loaded()
        self.assertEqual(c.mode, "normal")
        c.main.show.assert_called_once()

    def test_screen_removal_uses_visible_screen(self):
        self.assertEqual(
            position([Mock(x=0, y=0, width=1280, height=720)], {"x": 3000, "y": 100}, 460, 460),
            (410, 12, 460, 460),
        )

    def test_geometry_save_failure_cannot_keep_ai_running(self):
        c = self.controller()
        c.document.save.side_effect = OSError("fixture disk full")
        c.closing(c.main)
        c.session.request_stop.assert_called_once()

    def test_activity_uses_actual_stage_and_does_not_leak_cancel_objects(self):
        application = Mock()
        application.active_lock = threading.Lock()
        application.generation_lock = threading.Lock()
        application.import_lock = threading.Lock()
        application.active = {
            "a": {
                "cancel": threading.Event(),
                "stage": "考えています",
                "started": time.monotonic() - 2,
            }
        }
        application.last_activity = {"summary": "fixture", "files": ["a.py"], "edits": 1}
        application.startup_error = ""
        application.settings_copy.return_value = {"model": "fixture"}
        application.desktop_controller = None
        result = activity(application)
        self.assertEqual(result["stage"], "考えています")
        self.assertTrue(result["cancellable"])
        self.assertGreaterEqual(result["seconds"], 2)
        self.assertNotIn("cancel", result)
        application.active = {}
        self.assertFalse(activity(application)["cancellable"])
