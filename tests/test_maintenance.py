import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import maintenance
from desktop import DesktopSession
from unittest.mock import Mock
import threading


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.application = app.Application(self.root / "data")

    def tearDown(self):
        self.temp.cleanup()

    def test_backup_restore_includes_history_settings_and_undo_without_auth(self):
        application = self.application
        project = self.root / "project"
        project.mkdir()
        source = project / "a.py"
        source.write_text("answer = 1\n")
        identifier = application.store.create()
        application.store.add(identifier, "user", "私の会話")
        records, _ = application.attachments(str(project), ["a.py"])
        proposal = application.proposals(json.dumps({"edits": [{"path": "a.py", "old": "answer = 1", "new": "answer = 2"}]}), records)
        message = application.store.add(identifier, "assistant", "修正", proposal)
        change = application.apply_edit(message, 0)
        application.save_settings({"temperature": 0.4})
        (application.store.directory / "bonsai-api-key").write_text("secret-never-export")
        report = maintenance.backup(application)
        archive = application.store.directory / "exports" / report["name"]
        with zipfile.ZipFile(archive) as package:
            self.assertNotIn("bonsai-api-key", package.namelist())
            self.assertIn("backups/" + change["change_id"] + ".bak", package.namelist())
        application.store.delete(identifier)
        application.save_settings({"temperature": 0.9})
        restored = maintenance.restore(application, archive)
        self.assertTrue(restored["restart_required"])
        self.assertTrue((application.store.directory / "exports" / restored["retained"]).is_file())
        self.assertEqual(application.store.messages(identifier)[0]["content"], "私の会話")
        self.assertEqual(application.settings["temperature"], 0.4)
        application.undo(change["change_id"])
        self.assertEqual(source.read_text(), "answer = 1\n")

    def test_unsafe_backup_is_rejected_before_mutation(self):
        identifier = self.application.store.create()
        archive = self.root / "unsafe.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("../escape.txt", "bad")
        with self.assertRaises(ValueError):
            maintenance.restore(self.application, archive)
        self.assertEqual(self.application.store.conversations()[0]["id"], identifier)
        self.assertFalse((self.root / "escape.txt").exists())

    def test_logs_rotate_and_protect_undo(self):
        path = self.root / "engine.log"
        log = maintenance.BoundedLog(path, limit=1024, copies=2)
        for _ in range(100):
            log.write("x" * 300 + "\n")
        log.close()
        files = list(self.root.glob("engine.log*"))
        self.assertEqual(len(files), 3)
        self.assertLess(sum(file.stat().st_size for file in files), 4000)
        result = maintenance.storage(self.application.store.directory, self.root / "runtime")
        self.assertTrue(result["undo_protected"])

    def test_diagnostics_omit_chat_and_key_and_home_path(self):
        identifier = self.application.store.create()
        self.application.store.add(identifier, "assistant", "secret chat text", {"model": "model", "seconds": 2, "usage": {"completion_tokens": 20}})
        with patch.object(self.application, "models", return_value={"connected": False, "models": []}), patch.object(maintenance.shutil, "which", return_value=None):
            result = maintenance.diagnostics(self.application)
        text = json.dumps(result)
        self.assertNotIn("secret chat text", text)
        self.assertNotIn("token\"", text)
        self.assertEqual(result["performance"][0]["tokens_per_second"], 10)

    def test_failed_model_startup_opens_recovery_interface(self):
        server, window = Mock(server_port=8765), Mock()
        server.application.switch_profile.side_effect = RuntimeError("モデル不足")
        server.application.active_lock = threading.Lock()
        server.application.generation_lock = threading.Lock()
        server.application.active = {}
        from engines import PROFILES
        server.application.engines.configs.return_value = PROFILES
        session = DesktopSession(server, window, "bonsai")
        session.start()
        session.worker.join(timeout=2)
        self.assertTrue(session.ready.is_set())
        self.assertEqual(server.application.startup_error, "モデル不足")
        window.load_url.assert_called_once_with("http://127.0.0.1:8765")
        session.close()


if __name__ == "__main__":
    unittest.main()
