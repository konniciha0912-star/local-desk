"""Offline tests: filesystem safety, history, and end-to-end HTTP streaming."""
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import engines


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.file = self.project / "sample.py"
        self.file.write_bytes(b"def add(a, b):\r\n    return a - b\r\n")
        self.application = app.Application(self.root / "data")
        self.conversation = self.application.store.create()

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self):
        records, _ = self.application.attachments(str(self.project), ["sample.py"])
        content = json.dumps({"explanation": "Fix addition", "edits": [
            {"path": "sample.py", "old": "return a - b", "new": "return a + b"}]})
        return self.application.proposals(content, records)

    def saved_proposal(self):
        return self.application.store.add(self.conversation, "assistant", "", self.proposal())

    def test_endpoint_rejects_remote_and_invalid_ports(self):
        for url in ["https://localhost:1234/v1", "http://example.com/v1", "http://localhost:xyz/v1",
                    "http://localhost:99999/v1", "http://user:pass@localhost/v1", "http://localhost/v1?x=1"]:
            with self.subTest(url=url), self.assertRaises(app.AppError):
                app.local_endpoint(url)
        self.assertEqual(app.local_endpoint("http://127.0.0.1:1234/v1/"), "http://127.0.0.1:1234/v1")
        self.assertEqual(app.local_endpoint("http://[::1]:1234/v1"), "http://[::1]:1234/v1")

    def test_paths_exclude_escape_secrets_and_oversized_files(self):
        (self.project / ".env").write_text("secret")
        (self.project / "large.py").write_bytes(b"x" * (app.MAX_FILE + 1))
        for relative in ["../sample.py", str(self.file), ".env", "large.py", "missing.py"]:
            with self.subTest(path=relative), self.assertRaises(app.AppError):
                app.safe_file(self.project, relative)
        result = self.application.project(str(self.project))
        self.assertEqual([f["path"] for f in result["files"]], ["sample.py"])

    def test_symlink_rejected_if_available(self):
        linked = self.project / "linked.py"
        try:
            linked.symlink_to(self.file)
        except OSError:
            self.skipTest("OS does not permit symlink creation")
        with self.assertRaises(app.AppError):
            app.safe_file(self.project, "linked.py")

    def test_history_round_trip_and_delete(self):
        self.application.store.add(self.conversation, "user", "日本語の質問", {"mode": "chat"})
        self.application.store.add(self.conversation, "assistant", "回答")
        messages = self.application.store.messages(self.conversation)
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(self.application.store.conversations()[0]["title"], "日本語の質問")
        self.application.store.delete(self.conversation)
        with self.assertRaises(app.AppError):
            self.application.store.messages(self.conversation)

    def test_settings_persist_and_validate(self):
        self.application.save_settings({"temperature": 0.3, "max_tokens": 512})
        fresh = app.Application(self.root / "data")
        self.assertEqual(fresh.settings["max_tokens"], 512)
        for values in [{"temperature": float("nan")}, {"max_tokens": 99999}, {"max_tokens": 4096, "context_length": 4096}]:
            with self.assertRaises(app.AppError):
                self.application.save_settings(values)

    def test_profile_switch_persists_and_keeps_history(self):
        self.application.store.add(self.conversation, "user", "保持する会話")
        with patch.object(self.application.engines, "activate", return_value=engines.PROFILES["bonsai"]) as activate:
            result = self.application.switch_profile("bonsai")
            activate.assert_called_once_with("bonsai")
        self.assertEqual(result["endpoint"], "http://127.0.0.1:1235/v1")
        self.assertEqual(result["context_length"], 16384)
        self.assertEqual(app.Application(self.root / "data").settings["model"], "local-desk-bonsai")
        self.assertEqual(self.application.store.messages(self.conversation)[0]["content"], "保持する会話")

    def test_profile_failure_restores_engine_and_releases_lock(self):
        original = self.application.settings_copy()
        with patch.object(self.application.engines, "activate", side_effect=[RuntimeError("GPU failure"), engines.PROFILES["qwen"]]) as activate:
            with self.assertRaises(app.AppError):
                self.application.switch_profile("bonsai")
            self.assertEqual(activate.call_args_list[0].args, ("bonsai",))
            self.assertEqual(activate.call_args_list[1].args, ("qwen",))
        self.assertEqual(self.application.settings_copy(), original)
        self.assertTrue(self.application.generation_lock.acquire(blocking=False))
        self.application.generation_lock.release()
        with patch.object(self.application.engines, "activate") as activate:
            with self.assertRaises(app.AppError):
                self.application.switch_profile("arbitrary-command")
            activate.assert_not_called()

    def test_bonsai_payload_limits_thought_and_keeps_key_private(self):
        settings = {**app.DEFAULTS, **engines.PROFILES["bonsai"], "thinking": False}
        off = engines.inference_payload(settings, [{"role": "user", "content": "質問"}])
        self.assertEqual(off["reasoning_effort"], "none")
        self.assertNotIn("thinking_budget_tokens", off)
        settings["thinking"] = True
        on = engines.inference_payload(settings, [])
        self.assertEqual(on["reasoning_effort"], "medium")
        self.assertEqual(on["thinking_budget_tokens"], settings["max_tokens"] // 2)
        self.application.engines.key_path.write_text("test-local-key\n", encoding="ascii")
        self.assertEqual(self.application.engines.headers_for(settings["endpoint"]), {"Authorization": "Bearer test-local-key"})
        self.assertEqual(self.application.engines.headers_for("http://127.0.0.1:1234/v1"), {})
        self.assertNotIn("test-local-key", json.dumps(on))

    def test_runtime_is_not_a_reference_folder(self):
        runtime = self.project / ".runtime"
        runtime.mkdir()
        (runtime / "manifest.json").write_text('{"secret": "runtime"}')
        with self.assertRaises(app.AppError):
            app.safe_file(self.project, ".runtime/manifest.json")
        self.assertEqual([item["path"] for item in self.application.project(str(self.project))["files"]], ["sample.py"])

    def test_search_matches_message_body_and_treats_wildcards_literally(self):
        first = self.application.store.create()
        second = self.application.store.create()
        self.application.store.rename(first, "Python相談")
        self.application.store.rename(second, "別の相談")
        self.application.store.add(first, "assistant", "予算は50%で、変数はitem_countです。")
        self.application.store.add(second, "assistant", "予算は500円。itemXcountを使います。")
        for query in ["Python", "50%", "item_count"]:
            with self.subTest(query=query):
                self.assertEqual([row["id"] for row in self.application.store.conversations(query)], [first])
        self.assertEqual(self.application.store.conversations("不存在"), [])
        self.assertEqual(self.application.store.conversations("Python")[0]["message_count"], 1)

    def test_rename_persists_and_rejects_invalid_values(self):
        self.application.store.rename(self.conversation, "  新しい題名  ")
        self.application.store.add(self.conversation, "user", "最初の質問")
        fresh = app.Store(self.root / "data")
        self.assertEqual(fresh.conversations()[0]["title"], "新しい題名")
        for title in ["", " \n ", "長" * 121, None]:
            with self.subTest(title=title), self.assertRaises(app.AppError):
                fresh.rename(self.conversation, title)
        with self.assertRaises(app.AppError) as caught:
            fresh.rename("missing", "名前")
        self.assertEqual(caught.exception.status, 404)

    def test_edit_backup_and_undo_preserves_crlf(self):
        original = self.file.read_bytes()
        result = self.application.apply_edit(self.saved_proposal(), 0)
        saved = self.application.store.messages(self.conversation)[-1]["metadata"]["edits"][0]
        self.assertEqual(saved["change_id"], result["change_id"])
        self.assertFalse(saved["undone"])
        self.assertEqual(self.file.read_bytes(), original.replace(b"a - b", b"a + b"))
        self.application.undo(result["change_id"])
        self.assertTrue(self.application.store.messages(self.conversation)[-1]["metadata"]["edits"][0]["undone"])
        self.assertEqual(self.file.read_bytes(), original)
        with self.assertRaises(app.AppError):
            self.application.undo(result["change_id"])

    def test_encoding_and_bom_survive_edit(self):
        for encoding in ["utf-8-sig", "cp932"]:
            with self.subTest(encoding=encoding):
                self.file.write_bytes("# 日本語\r\nreturn a - b\r\n".encode(encoding))
                before = self.file.read_bytes()
                changed = self.application.apply_edit(self.saved_proposal(), 0)
                self.assertEqual(self.file.read_bytes(), "# 日本語\r\nreturn a + b\r\n".encode(encoding))
                self.application.undo(changed["change_id"])
                self.assertEqual(self.file.read_bytes(), before)

    def test_stale_edit_and_stale_undo_refuse_overwrite(self):
        proposal = self.saved_proposal()
        self.file.write_text("user changed it", encoding="utf-8")
        with self.assertRaises(app.AppError) as caught:
            self.application.apply_edit(proposal, 0)
        self.assertEqual(caught.exception.status, 409)
        self.file.write_text("return a - b", encoding="utf-8")
        changed = self.application.apply_edit(self.saved_proposal(), 0)
        self.file.write_text("another user change", encoding="utf-8")
        with self.assertRaises(app.AppError):
            self.application.undo(changed["change_id"])
        self.assertEqual(self.file.read_text(), "another user change")

    def test_bad_model_proposals_cannot_write_unselected_or_ambiguous_text(self):
        records, _ = self.application.attachments(str(self.project), ["sample.py"])
        for content in ['[]', '{"edits":[null]}', '{"edits":"wrong"}', 'not json',
                        '{"edits":[{"path":"other.py","old":"return a - b","new":"oops"}]}',
                        '{"edits":[{"path":"sample.py","old":" ","new":"oops"}]}']:
            with self.subTest(content=content):
                self.assertEqual(self.application.proposals(content, records)["edits"], [])

    def test_edit_requires_files_and_long_context_is_reported(self):
        body = {"conversation": self.conversation, "prompt": "fix", "mode": "edit"}
        with self.assertRaises(app.AppError):
            self.application.prepare(body)
        body.update(mode="chat", prompt="日本語" * 6000)
        prepared = self.application.prepare(body)
        self.assertTrue(prepared[4])
        self.assertLess(len(prepared[-1][-1]["content"].encode()), 8192)


class MockModel(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps({"data": [{"id": "mock-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        prompt = body["messages"][-1]["content"]
        if prompt == "fail":
            self.send_error(503, "Model unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [{"choices": [{"delta": {"reasoning_content": "thinking"}}]},
                  {"choices": [{"delta": {"content": "日本語の回答"}}]},
                  {"choices": [], "usage": {"completion_tokens": 7, "prompt_tokens": 5}}]
        try:
            for chunk in chunks:
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
                self.wfile.flush()
                if prompt == "slow":
                    time.sleep(0.3)
            self.wfile.write(b"data: [DONE]\n\n")
        except ConnectionError:
            pass


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = ThreadingHTTPServer(("127.0.0.1", 0), MockModel)
        self.model.daemon_threads = True
        self.model.requests = []
        self.server = app.make_server(0, self.temp.name)
        self.server.application.save_settings({"endpoint": f"http://127.0.0.1:{self.model.server_port}/v1", "model": "mock-model"})
        self.threads = []
        for server in [self.model, self.server]:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.server.application.token
        self.conversation = self.server.application.store.create()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        for server in [self.server, self.model]:
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(2)
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        options = {"X-App-Token": self.token}
        options.update(headers or {})
        raw = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=raw, headers=options)
        return self.opener.open(request, timeout=5)

    def chat_body(self, prompt="hello"):
        return {"conversation": self.conversation, "prompt": prompt, "mode": "chat", "request_id": "test-request"}

    def test_static_bootstrap_and_csrf_guards(self):
        with self.request("/") as response:
            self.assertIn(b"Local Desk", response.read())
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        with self.request("/api/bootstrap", headers={"X-App-Token": ""}) as response:
            self.assertEqual(json.load(response)["token"], self.token)
        for headers in [{"X-App-Token": "wrong"}, {"Origin": "https://evil.example"}, {"Host": "evil.example"}]:
            with self.subTest(headers=headers), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request("/api/conversations", {}, headers)
            self.assertEqual(caught.exception.code, 403)
            caught.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/../app.py")
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_stream_and_persist_reasoning_usage(self):
        with self.request("/api/chat", self.chat_body()) as response:
            result = response.read().decode()
        self.assertIn("event: delta", result)
        self.assertIn("event: done", result)
        messages = self.server.application.store.messages(self.conversation)
        self.assertEqual(messages[-1]["content"], "日本語の回答")
        self.assertEqual(messages[-1]["metadata"]["reasoning"], "thinking")
        self.assertEqual(messages[-1]["metadata"]["usage"]["completion_tokens"], 7)
        self.assertEqual(self.model.requests[-1]["reasoning_effort"], "none")
        self.assertFalse(self.server.application.generation_lock.locked())

    def test_rename_and_search_over_http(self):
        self.server.application.store.add(self.conversation, "assistant", "回答本文に秘密の合言葉")
        with self.request("/api/rename", {"id": self.conversation, "title": "整理した会話"}) as response:
            self.assertEqual(json.load(response)["title"], "整理した会話")
        from urllib.parse import quote
        with self.request("/api/conversations?q=" + quote("合言葉")) as response:
            rows = json.load(response)
        self.assertEqual(rows[0]["title"], "整理した会話")
        self.assertEqual(rows[0]["message_count"], 1)

    def test_profile_switch_api_and_busy_guard(self):
        with self.request("/api/profiles") as response:
            self.assertEqual({item["id"] for item in json.load(response)}, {"qwen", "ornith", "bonsai"})
        with patch.object(self.server.application.engines, "activate", return_value=engines.PROFILES["bonsai"]) as activate:
            with self.request("/api/profile", {"profile": "bonsai"}) as response:
                settings = json.load(response)
            self.assertEqual(settings["model"], "local-desk-bonsai")
            self.assertTrue(settings["thinking"])
            activate.assert_called_once_with("bonsai")
            self.server.application.generation_lock.acquire()
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request("/api/profile", {"profile": "qwen"})
                self.assertEqual(caught.exception.code, 409)
                caught.exception.close()
                self.assertEqual(activate.call_count, 1)
            finally:
                self.server.application.generation_lock.release()

    def test_model_failure_is_saved_and_next_request_works(self):
        with self.request("/api/chat", self.chat_body("fail")) as response:
            self.assertIn("event: error", response.read().decode())
        with self.request("/api/chat", self.chat_body()) as response:
            self.assertIn("event: done", response.read().decode())
        self.assertEqual(len(self.server.application.store.messages(self.conversation)), 4)

    def test_cancel_and_reject_concurrent_chat_and_delete(self):
        response = self.request("/api/chat", self.chat_body("slow"))
        # The SSE start event is emitted before the upstream request begins.
        response.readline()
        for path, body in [("/api/chat", self.chat_body()), ("/api/delete", {"id": self.conversation}),
                           ("/api/rename", {"id": self.conversation, "title": "変更"}),
                           ("/api/profile", {"profile": "bonsai"}), ("/api/settings", {"temperature": 0.3})]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(path, body)
            self.assertEqual(caught.exception.code, 409)
            caught.exception.close()
        with self.request("/api/cancel", {"request_id": "test-request"}) as cancelled:
            self.assertTrue(json.load(cancelled)["ok"])
        response.read()
        response.close()
        messages = self.server.application.store.messages(self.conversation)
        self.assertTrue(messages[-1]["metadata"]["cancelled"])
        self.assertFalse(self.server.application.generation_lock.locked())


if __name__ == "__main__":
    unittest.main()
