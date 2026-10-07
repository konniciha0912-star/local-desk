"""Service contracts with synthetic accounts; no real messages are sent."""

import base64
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import urllib.parse
from unittest.mock import Mock, patch
from connections import Connections, Vault, remote, gmail_text


class MemoryVault:
    def __init__(self):
        self.values = {}

    def load(self):
        return json.loads(json.dumps(self.values))

    def update(self, k, v):
        if v is None:
            self.values.pop(k, None)
        else:
            self.values[k] = v


class ConnectionTests(unittest.TestCase):
    def service(self):
        c = Connections(Path("unused"))
        c.vault = MemoryVault()
        self.addCleanup(c.close)
        return c

    def test_no_service_or_secret_access_on_initialization_and_status(self):
        with patch("connections.remote") as call:
            c = self.service()
            c.vault.values["github"] = {"token": "private-fixture", "account": "fixture"}
            status = json.dumps(c.status())
            self.assertNotIn("private-fixture", status)
            call.assert_not_called()

    def test_account_ciphertext_is_excluded_from_user_backup(self):
        import app, maintenance, zipfile

        with tempfile.TemporaryDirectory() as temporary:
            application = app.Application(Path(temporary))
            (Path(temporary) / "connections.enc").write_bytes(b"fixture-ciphertext")
            result = maintenance.backup(application)
            with zipfile.ZipFile(Path(temporary) / "exports" / result["name"]) as archive:
                self.assertNotIn("connections.enc", archive.namelist())
                self.assertNotIn(
                    b"fixture-ciphertext", b"".join(archive.read(n) for n in archive.namelist())
                )
            application.connections.close()

    def test_outbound_allowlist_rejects_redirects_credentials_and_local_targets(self):
        for address in [
            "http://api.github.com/user",
            "https://127.0.0.1/",
            "https://api.github.com.evil.test/",
            "https://secret@api.github.com/user",
            "https://api.github.com:444/user",
        ]:
            with self.subTest(address=address), self.assertRaises(ValueError):
                remote(address)

    def test_issue_exact_preview_single_use_no_arbitrary_operation(self):
        c = self.service()
        c.vault.values["github"] = {"token": "fixture-token"}
        with patch(
            "connections.remote",
            return_value={"html_url": "https://github.com/fixture/repo/issues/1"},
        ) as call:
            preview = c.preview(
                {
                    "service": "github",
                    "target": "fixture/repo",
                    "title": "Fixture",
                    "text": "original",
                }
            )
            call.assert_not_called()
            with self.assertRaises(ValueError):
                c.commit({"id": preview["id"]})
            c.commit({"id": preview["id"], "confirmed": True, "text": "tampered"})
            self.assertEqual(
                call.call_args.kwargs["body"], {"title": "Fixture", "body": "original"}
            )
            with self.assertRaises(ValueError):
                c.commit({"id": preview["id"], "confirmed": True})
            call.assert_called_once()
        for target in ["../x", "fixture/repo/issues", "https://github.com/x/y"]:
            with self.assertRaises(ValueError):
                c.preview({"service": "github", "target": target, "title": "x", "text": "y"})

    def test_gmail_only_creates_draft_with_scope_and_header_validation(self):
        c = self.service()
        c.vault.values["google"] = {
            "refresh_token": "fixture",
            "access_token": "fixture",
            "expires_at": time.time() + 3600,
            "scope": "https://www.googleapis.com/auth/gmail.compose",
        }
        with patch("connections.remote", return_value={"id": "draft-fixture"}) as call:
            preview = c.preview(
                {
                    "service": "gmail",
                    "target": "fixture@example.test",
                    "title": "日本語の件名",
                    "text": "本文",
                }
            )
            result = c.commit({"id": preview["id"], "confirmed": True})
            self.assertIn("送信していません", result["message"])
            self.assertTrue(call.call_args.args[0].endswith("/drafts"))
            raw = base64.urlsafe_b64decode(call.call_args.kwargs["body"]["message"]["raw"])
            self.assertIn(b"fixture@example.test", raw)
        with self.assertRaises(ValueError):
            c.preview(
                {
                    "service": "gmail",
                    "target": "a@example.test",
                    "title": "x\r\nBcc: other",
                    "text": "y",
                }
            )

    def test_video_metadata_never_injects_embed_html_or_claims_audio(self):
        c = self.service()
        with patch(
            "connections.remote",
            return_value={
                "title": "fixture",
                "author_name": "tester",
                "html": "<script>evil</script>",
            },
        ) as call:
            data = c.video({"url": "https://youtu.be/abcdefghijk", "confirmed": True})
            self.assertNotIn("<script>", json.dumps(data))
            self.assertIn("未取得", data["items"][0]["text"])
            call.assert_called_once()
        with patch("connections.remote") as call:
            data = c.video({"url": "https://www.instagram.com/reels/Fixture/", "confirmed": True})
            call.assert_not_called()
            self.assertIn("リンクのみ", data["items"][0]["text"])
        with self.assertRaises(ValueError):
            c.video({"url": "https://127.0.0.1/", "confirmed": True})

    def test_unavailable_video_service_preserves_reference_and_reports_failure(self):
        c = self.service()
        with patch("connections.remote", side_effect=ValueError("サービスAPIがエラーを返しました (503)。")):
            data = c.video({"url": "https://www.tiktok.com/@fixture/video/1234567890", "confirmed": True})
        self.assertIn("503", data["warning"])
        self.assertIn("リンクのみ", data["items"][0]["text"])
        self.assertIn("内容は取得していません", data["items"][0]["text"])

    def test_external_ai_explicit_consent_and_prompt_only(self):
        c = self.service()
        c.vault.values["openai"] = {"token": "fixture-key", "model": "fixture-model"}
        with patch(
            "connections.remote",
            return_value={"output": [{"content": [{"type": "output_text", "text": "answer"}]}]},
        ) as call:
            with self.assertRaises(ValueError):
                c.ai({"service": "openai", "prompt": "question"})
            call.assert_not_called()
            result = c.ai({"service": "openai", "prompt": "question", "confirmed": True})
            self.assertEqual(result["items"][0]["text"], "answer")
            self.assertFalse(call.call_args.kwargs["body"]["store"])
            self.assertEqual(call.call_args.kwargs["body"]["input"], "question")

    def test_pkce_callback_rejects_wrong_state_replay_and_expiration(self):
        c = self.service()
        c.vault.values["google-client"] = {
            "client_id": "fixture.apps.googleusercontent.com",
            "client_secret": "fixture-secret",
        }
        start = c._oauth_start({"services": ["gmail"]})
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(start["url"]).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotIn("client_secret", params)
        with patch(
            "connections.remote",
            return_value={
                "refresh_token": "fixture-refresh",
                "access_token": "fixture-access",
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            },
        ) as call:
            with self.assertRaises(ValueError):
                c._oauth_finish({"state": ["wrong"], "code": ["code"]})
            call.assert_not_called()
            state = c.pending["state"]
            c._oauth_finish({"state": [state], "code": ["code"]})
            self.assertIn("code_verifier", call.call_args.kwargs["form"])
            with self.assertRaises(ValueError):
                c._oauth_finish({"state": [state], "code": ["code"]})
        c.close()
        self.assertFalse(c.oauth_thread.is_alive())

    def test_google_refresh_keeps_refresh_token_and_connection_scope(self):
        c = self.service()
        c.vault.values.update(
            {
                "google-client": {"client_id": "fixture", "client_secret": "fixture"},
                "google": {
                    "refresh_token": "original",
                    "scope": "https://www.googleapis.com/auth/gmail.readonly",
                    "expires_at": 0,
                },
            }
        )
        with patch(
            "connections.remote", return_value={"access_token": "fresh", "expires_in": 3600}
        ):
            self.assertEqual(c._google_token(), "fresh")
        self.assertEqual(c.vault.values["google"]["refresh_token"], "original")

    @unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
    def test_windows_vault_ciphertext_roundtrip_and_no_plaintext_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Vault(temporary)
            vault.update("github", {"token": "private-fixture-token"})
            self.assertNotIn(b"private-fixture-token", vault.path.read_bytes())
            self.assertEqual(Vault(temporary).load()["github"]["token"], "private-fixture-token")
            vault.update("github", None)
            self.assertEqual(vault.load(), {})
            self.assertEqual(len(list(Path(temporary).iterdir())), 1)

    def test_gmail_plain_text_nested_mime(self):
        data = base64.urlsafe_b64encode("日本語の本文".encode()).decode().rstrip("=")
        self.assertEqual(
            gmail_text(
                {
                    "mimeType": "multipart/alternative",
                    "parts": [{"mimeType": "text/plain", "body": {"data": data}}],
                }
            ),
            "日本語の本文",
        )
