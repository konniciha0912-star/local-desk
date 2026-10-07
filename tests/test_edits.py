import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from edits import apply_batch, diff_hunks, partial_edit


class EditBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.project = self.root / "project"; self.project.mkdir()
        self.a, self.b = self.project / "a.py", self.project / "b.py"
        self.a.write_bytes(b"a = 1\nb = 2\n"); self.b.write_bytes(b"c = 3\n")
        self.application = app.Application(self.root / "data")
        self.conversation = self.application.store.create()

    def tearDown(self): self.temp.cleanup()

    def proposal(self, edits):
        records, _ = self.application.attachments(str(self.project), ["a.py", "b.py"])
        metadata = self.application.proposals(json.dumps({"explanation":"test", "edits":edits}), records)
        return self.application.store.add(self.conversation, "assistant", "", metadata)

    def test_same_file_multiple_edits_and_other_file_apply_and_undo_together(self):
        message = self.proposal([{"path":"a.py","old":"a = 1","new":"a = 10"}, {"path":"a.py","old":"b = 2","new":"b = 20"}, {"path":"b.py","old":"c = 3","new":"c = 30"}])
        result = apply_batch(self.application, message, [0,1,2])
        self.assertEqual(len(result["changes"]), 2)
        self.assertEqual(self.a.read_bytes(), b"a = 10\nb = 20\n")
        self.application.undo(result["change_id"])
        self.assertEqual(self.a.read_bytes(), b"a = 1\nb = 2\n")
        self.assertEqual(self.b.read_bytes(), b"c = 3\n")
        self.assertTrue(all(edit["undone"] for edit in self.application.store.messages(self.conversation)[0]["metadata"]["edits"]))

    def test_write_failure_rolls_back_files_and_database(self):
        message = self.proposal([{"path":"a.py","old":"a = 1","new":"a = 10"}, {"path":"b.py","old":"c = 3","new":"c = 30"}])
        replace = Path.replace
        def failing(path, target):
            if Path(target).resolve() == self.b.resolve() and ".localdesk-" in path.name: raise OSError("simulated disk failure")
            return replace(path, target)
        with patch.object(Path, "replace", new=failing), self.assertRaises(OSError): apply_batch(self.application, message, [0,1])
        self.assertEqual(self.a.read_bytes(), b"a = 1\nb = 2\n")
        self.assertEqual(self.b.read_bytes(), b"c = 3\n")
        with self.application.store.connect() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 0)
        self.assertFalse((self.root / "data/batch-journal.json").exists())

    def test_bad_syntax_or_stale_file_cannot_partially_apply(self):
        message = self.proposal([{"path":"a.py","old":"a = 1","new":"a = 10"}, {"path":"b.py","old":"c = 3","new":"c = ("}])
        with self.assertRaises(app.AppError): apply_batch(self.application, message, [0,1])
        self.assertEqual(self.a.read_bytes(), b"a = 1\nb = 2\n")
        self.b.write_bytes(b"c = 99\n")
        with self.assertRaises(app.AppError): apply_batch(self.application, message, [0,1])
        self.assertEqual(self.a.read_bytes(), b"a = 1\nb = 2\n")

    def test_overlapping_proposals_are_rejected(self):
        message = self.proposal([{"path":"a.py","old":"a = 1","new":"a = 10"}, {"path":"a.py","old":"= 1","new":"= 30"}])
        with self.assertRaises(app.AppError): apply_batch(self.application, message, [0,1])

    def test_new_file_requires_approved_path_and_undo_removes_it(self):
        prepared = self.application.prepare({"conversation":self.conversation,"prompt":"create","mode":"edit", "root":str(self.project), "new_paths":["new.py"]})
        metadata = self.application.proposals(json.dumps({"edits":[{"path":"new.py","old":"","new":"answer = 42\n"},{"path":"evil.py","old":"","new":"unapproved"}]}), prepared[3])
        self.assertEqual(len(metadata["edits"]), 1)
        message = self.application.store.add(self.conversation,"assistant","",metadata)
        result = apply_batch(self.application,message,[0])
        self.assertEqual((self.project / "new.py").read_bytes(),b"answer = 42\n")
        self.application.undo(result["change_id"])
        self.assertFalse((self.project / "new.py").exists())
        self.assertFalse((self.project / "evil.py").exists())

    def test_new_file_cannot_overwrite_existing_or_escape(self):
        for relative in ["a.py","../escape.py",".env.py",".runtime/a.py", "file.exe"]:
            with self.subTest(path=relative), self.assertRaises(app.AppError): app.safe_create(self.project,relative)

    def test_hunk_selection_preserves_unselected_changes(self):
        old = "\n".join(f"value_{index} = {index}" for index in range(20)) + "\n"
        new = old.replace("value_1 = 1", "value_1 = 100").replace("value_18 = 18", "value_18 = 1800")
        edit = {"old":old,"new":new}
        self.assertEqual(len(diff_hunks(edit)),2)
        chosen = partial_edit(edit,[1])["new"]
        self.assertIn("value_1 = 1\n", chosen)
        self.assertIn("value_18 = 1800", chosen)

    def test_double_encoded_json_is_normalized_only_when_original_matches(self):
        message=self.proposal([{"path":"a.py","old":"a = 1\\nb = 2\\n","new":"a = 10\\nb = 20\\n"}])
        metadata=self.application.store.get_message(message)["metadata"]
        self.assertTrue(metadata["normalized_json_strings"])
        apply_batch(self.application,message,[0]);self.assertEqual(self.a.read_bytes(),b"a = 10\nb = 20\n")
        self.a.write_bytes(b'literal = "a\\nb"\n')
        records,_=self.application.attachments(str(self.project),["a.py"])
        proposal=self.application.proposals(json.dumps({"edits":[{"path":"a.py","old":"a\\nb","new":"c\\nd"}]}),records)
        self.assertFalse(proposal["normalized_json_strings"]);self.assertEqual(proposal["edits"][0]["new"],"c\\nd")

    def test_model_lf_or_double_encoding_matches_windows_crlf_without_losing_eol(self):
        self.a.write_bytes(b"a = 1\r\nb = 2\r\n")
        for old,new in [("a = 1\nb = 2\n","a = 10\nb = 20\n"),("a = 1\\nb = 2\\n","a = 10\\nb = 20\\n")]:
            message=self.proposal([{"path":"a.py","old":old,"new":new}]);metadata=self.application.store.get_message(message)["metadata"]
            self.assertEqual(metadata["edits"][0]["old"],"a = 1\r\nb = 2\r\n")
            result=apply_batch(self.application,message,[0]);self.assertEqual(self.a.read_bytes(),b"a = 10\r\nb = 20\r\n");self.application.undo(result["change_id"])

    def test_interrupted_apply_recovers_original_and_keeps_history(self):
        before = self.a.read_bytes(); after = b"a = 99\nb = 2\n"
        backup = self.root / "data/backups" / ("a"*32 + ".bak"); backup.parent.mkdir(); backup.write_bytes(before)
        self.a.write_bytes(after)
        journal = self.root / "data/batch-journal.json"
        journal.write_text(json.dumps({"operation":"apply","batch":"b"*32,"files":[{"id":"a"*32,"path":str(self.a),"backup":str(backup),"before_hash":app.sha(before),"after_hash":app.sha(after),"temporary":str(self.a)+".pending"}]}))
        fresh = app.Application(self.root / "data")
        self.assertEqual(self.a.read_bytes(),before)
        self.assertFalse(journal.exists())
        self.assertTrue(fresh.notices)
        self.assertEqual(len(fresh.store.conversations()),1)


if __name__ == "__main__": unittest.main()
