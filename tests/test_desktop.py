"""Lifecycle tests without a GUI, model or Windows dependency."""
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from desktop import DesktopSession, visible_geometry
from engines import EngineManager, PROFILES, profile_for


class DesktopTests(unittest.TestCase):
    def test_geometry_keeps_title_accessible_after_monitor_removal(self):
        screen = Mock(x=0, y=0, width=1920, height=1080)
        value = {"x": 3000, "y": 200, "width": 1180, "height": 800}
        self.assertIsNone(visible_geometry(value, [screen])["x"])
        value["x"] = 100
        self.assertEqual(visible_geometry(value, [screen])["x"], 100)
        left = Mock(x=-1920, y=0, width=1920, height=1080)
        value["x"] = -1700
        self.assertEqual(visible_geometry(value, [screen, left])["x"], -1700)
    def server(self):
        server = Mock(server_port=8765)
        server.application.active_lock = threading.Lock()
        server.application.generation_lock = threading.Lock()
        server.application.active = {}
        server.application.engines.configs.return_value = PROFILES
        return server

    def test_close_during_startup_cannot_open_page_or_http_server(self):
        server, window = self.server(), Mock()
        session = DesktopSession(server, window, 'bonsai')
        entered, released = threading.Event(), threading.Event()
        def prepare(profile):
            entered.set()
            self.assertTrue(released.wait(timeout=3))
        server.application.switch_profile.side_effect = prepare
        session.start()
        self.assertTrue(entered.wait(timeout=3))
        session.request_stop()
        released.set()
        session.close()
        window.load_url.assert_not_called()
        server.serve_forever.assert_not_called()
        server.application.engines.close.assert_called_once()
        self.assertFalse(session.worker.is_alive())

    def test_close_cancels_stream_before_engine_cleanup(self):
        server = self.server()
        cancelled = threading.Event()
        server.application.active = {'request': {'cancel': cancelled}}
        server.application.engines.close.side_effect = lambda: self.assertTrue(cancelled.is_set())
        DesktopSession(server, Mock(), 'qwen').close()
        self.assertTrue(cancelled.is_set())
        server.server_close.assert_called_once()

    def test_shutdown_prevents_late_model_launch(self):
        engine = EngineManager(Path('.'), Path('.'))
        engine.request_shutdown()
        with self.assertRaisesRegex(RuntimeError, '終了'):
            engine.activate('bonsai')

    def test_profiles_recognize_shared_desktop_engine(self):
        for name in PROFILES:
            config = dict(PROFILES[name], endpoint=PROFILES['bonsai']['endpoint'])
            self.assertEqual(profile_for(config), name)
        self.assertEqual(profile_for({'model':'local-desk-qwen','endpoint':'http://127.0.0.1:9999/v1'}), '')


if __name__ == '__main__':
    unittest.main()
