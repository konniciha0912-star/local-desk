import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from context import TokenCounter
from productivity import Templates


class ContextProductivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.application = app.Application(self.root / "data")
        self.identifier = self.application.store.create()

    def tearDown(self):
        self.temp.cleanup()

    def body(self, **changes):
        return {"conversation": self.identifier, "prompt": "質問", "mode": "chat", **changes}

    def test_legacy_schema_migration_keeps_history(self):
        data = self.root / "old"
        data.mkdir()
        db = sqlite3.connect(data / "history.sqlite3")
        db.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT,updated REAL)")
        db.execute("INSERT INTO conversations VALUES('old','元の題名',1)")
        db.commit(); db.close()
        store = app.Store(data)
        self.assertEqual(store.conversations()[0]["title"], "元の題名")
        store.create()
        self.assertEqual(len(store.conversations()), 2)

    def test_pin_tags_trash_and_restore_keep_original_messages(self):
        store = self.application.store
        second = store.create()
        store.add(self.identifier, "user", "保持する文章")
        store.organize(self.identifier, {"pinned": True, "tags": ["仕事", "Python"]})
        store.add(second, "user", "新しい質問")
        self.assertEqual(store.conversations()[0]["id"], self.identifier)
        self.assertEqual(store.conversations("仕事")[0]["id"], self.identifier)
        store.delete(self.identifier)
        self.assertEqual(store.conversations(trash=True)[0]["id"], self.identifier)
        with self.assertRaises(app.AppError): store.messages(self.identifier)
        store.organize(self.identifier, {"restore": True})
        self.assertEqual(store.messages(self.identifier)[0]["content"], "保持する文章")

    def test_fork_retains_original_and_never_copies_applicable_edits(self):
        store = self.application.store
        store.add(self.identifier, "user", "質問")
        answer = store.add(self.identifier, "assistant", "回答", {"edits": [{"path": "unsafe"}]})
        store.add(self.identifier, "user", "後の質問")
        fork = store.fork(self.identifier, answer)["id"]
        self.assertEqual(len(store.messages(self.identifier)), 3)
        self.assertEqual(len(store.messages(fork)), 2)
        self.assertNotIn("edits", store.messages(fork)[-1]["metadata"])
        self.assertEqual(store.messages(store.fork(self.identifier, "__start__")["id"]), [])

    def test_preview_respects_requested_lines_history_and_changed_source(self):
        project = self.root / "project"; project.mkdir()
        source = project / "facts.txt"; source.write_text("one\ntwo\nthree\nfour\n")
        self.application.store.add(self.identifier, "user", "以前の質問")
        self.application.store.add(self.identifier, "assistant", "以前の回答")
        body = self.body(root=str(project), files=["facts.txt"], ranges={"facts.txt": {"start": 2, "end": 3}}, history_limit=0)
        prepared, scope = self.application.prepare_details(body)
        self.assertEqual(scope["history_used"], 0)
        self.assertEqual(scope["sources"][0]["included_end"], 3)
        current = prepared[-1][-1]["content"]
        self.assertIn("two\nthree\n", current.replace("\r\n", "\n"))
        self.assertNotIn("four\n", current.replace("\r\n", "\n"))
        body["preview_digest"] = scope["digest"]
        source.write_text("one\nchanged\nthree\nfour\n")
        with self.assertRaises(app.AppError) as caught: self.application.prepare_details(body)
        self.assertEqual(caught.exception.status, 409)

    def test_model_tokenizer_includes_formatted_chat_and_caches(self):
        settings = dict(app.DEFAULTS, endpoint="http://127.0.0.1:1235/v1")
        counter = TokenCounter(Mock(standalone=True), settings)
        with patch.object(counter, "post", side_effect=[{"prompt": "formatted"}, {"tokens": [1, 2, 3]}]) as post:
            self.assertEqual(counter.count([{"role": "user", "content": "質問"}]), 3)
            self.assertEqual(counter.count([{"role": "user", "content": "質問"}]), 3)
            self.assertEqual(post.call_count, 2)
            self.assertEqual(post.call_args.args[1]["content"], "formatted")

    def test_summary_replaces_input_context_but_preserves_source_history(self):
        store = self.application.store
        store.add(self.identifier, "user", "元の長い文章")
        answer = store.add(self.identifier, "assistant", "重要な要約")
        with store.connect() as db:
            db.execute("UPDATE conversations SET compact_message=? WHERE id=?", (answer, self.identifier))
        _, scope = self.application.prepare_details(self.body())
        self.assertIn("重要な要約", scope["messages"][0]["content"])
        self.assertEqual(scope["history_used"], 0)
        self.assertEqual(store.messages(self.identifier)[0]["content"], "元の長い文章")

    def test_templates_persist_and_invalid_input_cannot_replace_good_data(self):
        templates = self.application.templates
        item = templates.save({"name": "自分の依頼", "mode": "translate", "prompt": "英訳する: {input}"})
        fresh = Templates(self.root / "data")
        self.assertIn(item, fresh.items)
        with self.assertRaises(ValueError): fresh.save({"name": "bad", "mode": "execute", "prompt": "run"})
        self.assertIn(item, Templates(self.root / "data").items)

    def test_task_preset_is_request_local(self):
        before = self.application.settings_copy()
        prepared = self.application.prepare(self.body(task_preset="careful"))
        self.assertTrue(prepared[5]["thinking"])
        self.assertEqual(prepared[5]["max_tokens"], 3072)
        self.assertEqual(self.application.settings_copy(), before)


if __name__ == "__main__": unittest.main()
