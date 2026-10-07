"""Recovery and preference tests; no real models or download traffic."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import engines
import setup_bonsai

class ExtractionValidationTests(unittest.TestCase):
    def test_items_reject_nested_nonfinite_and_unexpected_fields(self):
        valid={"items":[{"name":"時刻","value":"10:00","source":"10時から"}]}
        self.assertEqual(engines.validate_extracted(valid),valid)
        for bad in [{"items":[{}]}, {"items":[],"extra":True},
                    {"items":[{"name":"x","value":{},"source":"x"}]},
                    {"items":[{"name":"x","value":float('nan'),"source":"x"}]}]:
            with self.assertRaises(ValueError): engines.validate_extracted(bad)


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_corrupt_settings_restore_previous_valid_copy_and_preserve_original(self):
        application = app.Application(self.root)
        application.save_settings({"max_tokens": 512})
        application.save_settings({"max_tokens": 768})
        application.settings_path.write_text("{bad json", encoding="utf-8")
        restored = app.Application(self.root)
        self.assertEqual(restored.settings["max_tokens"], 512)
        self.assertTrue(restored.notices)
        self.assertEqual(next(self.root.glob("settings.json.corrupt-*")).read_text(), "{bad json")

    def test_invalid_numeric_settings_use_defaults(self):
        for value in ["Infinity", "NaN", -1, None]:
            path = self.root / "settings.json"
            path.write_text(json.dumps({"max_tokens": value}))
            restored = app.Application(self.root)
            self.assertEqual(restored.settings, app.DEFAULTS)
            self.assertTrue(restored.notices)
        with self.assertRaises(app.AppError):
            restored.save_settings({"max_tokens": float("inf")})

    def test_model_preferences_survive_switch_restart_and_reset(self):
        application = app.Application(self.root)
        with patch.object(application.engines, "activate", side_effect=lambda name: engines.PROFILES[name]):
            application.switch_profile("qwen")
            application.save_settings({"temperature": 0.2, "max_tokens": 384, "thinking": True})
            application.switch_profile("bonsai")
            application.save_settings({"temperature": 0.8, "max_tokens": 1024})
            application.switch_profile("ornith")
            application.switch_profile("qwen")
        fresh = app.Application(self.root)
        self.assertEqual(fresh.settings["max_tokens"], 384)
        with patch.object(fresh.engines, "activate", side_effect=lambda name: engines.PROFILES[name]):
            fresh.switch_profile("bonsai")
            self.assertEqual(fresh.settings["max_tokens"], 1024)
            fresh.switch_profile("qwen", reset=True)
        self.assertEqual(fresh.settings["temperature"], 0.6)
        self.assertEqual(fresh.settings["max_tokens"], 1536)
        self.assertFalse(fresh.settings["thinking"])

    def test_managed_context_cannot_drift_from_engine(self):
        application = app.Application(self.root)
        with self.assertRaises(app.AppError):
            application.save_settings({"context_length": 32768})
        with patch.object(application.engines, "activate", return_value=engines.PROFILES["bonsai"]):
            application.switch_profile("bonsai")
        self.assertEqual(application.settings["context_length"], 16384)

    def test_errors_offer_cause_specific_recovery(self):
        self.assertIn("メモリ", app.readable_engine_error(RuntimeError("CUDA out of memory")))
        self.assertIn("新しい会話", app.readable_engine_error(RuntimeError("context length exceeded")))
        self.assertIn("認証", app.readable_engine_error(urllib.error.HTTPError("local", 401, "bad", {}, None)))

    def download(self, initial, response_bytes, status=200):
        target = self.root / "model.gguf"
        partial = target.with_suffix(".gguf.part")
        partial.write_bytes(initial)
        expected_bytes = b"a valid model"
        response = io.BytesIO(response_bytes)
        response.status = status
        with patch.object(setup_bonsai.urllib.request, "urlopen", return_value=response) as request:
            setup_bonsai.download("https://example.invalid/model", target, len(expected_bytes), hashlib.sha256(expected_bytes).hexdigest())
        self.assertEqual(target.read_bytes(), expected_bytes)
        return request

    def test_completed_partial_is_verified_without_network(self):
        request = self.download(b"a valid model", b"")
        request.assert_not_called()

    def test_resume_uses_range_and_appends(self):
        request = self.download(b"a valid ", b"model", 206)
        self.assertEqual(request.call_args.args[0].get_header("Range"), "bytes=8-")

    def test_oversized_or_bad_complete_partial_is_preserved(self):
        for invalid in [b"a valid model extra", b"bad model!!!!"]:
            with self.subTest(data=invalid):
                (self.root / "model.gguf").unlink(missing_ok=True)
                self.download(invalid, b"a valid model")
        self.assertEqual(len(list(self.root.glob("*.failed-*"))), 2)


if __name__ == "__main__":
    unittest.main()
