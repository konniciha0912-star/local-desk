"""Start and switch only the local inference engines managed by Local Desk."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import secrets
import subprocess
import threading
import time
import urllib.request
from maintenance import BoundedLog

PROFILES = {
    "bonsai": {"label": "Bonsai 2 · 27B", "model": "local-desk-bonsai", "endpoint": "http://127.0.0.1:1235/v1", "context_length": 16384, "temperature": 0.7, "max_tokens": 2048, "thinking": True},
    "qwen": {"label": "Qwen3.5 · 9B", "model": "local-desk-qwen", "endpoint": "http://127.0.0.1:1234/v1", "context_length": 8192, "temperature": 0.6, "max_tokens": 1536, "thinking": False},
    "ornith": {"label": "Ornith 1.5 · 9B", "model": "local-desk-ornith", "endpoint": "http://127.0.0.1:1234/v1", "context_length": 8192, "temperature": 0.6, "max_tokens": 1536, "thinking": False},
}
EDIT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["explanation", "edits"], "properties": {
    "explanation": {"type": "string"}, "edits": {"type": "array", "maxItems": 12, "items": {
        "type": "object", "additionalProperties": False, "required": ["path", "old", "new"],
        "properties": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}}}}}}
EXTRACT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["items"], "properties": {"items": {
    "type": "array", "maxItems": 100, "items": {"type": "object", "additionalProperties": False, "required": ["name", "value", "source"],
    "properties": {"name": {"type": "string"}, "value": {"type": ["string", "number", "boolean", "null"]}, "source": {"type": "string"}}}}}}

def profile_for(settings):
    model = settings.get("model", "")
    if model.startswith("local-desk-custom-") and settings.get("endpoint") == PROFILES["bonsai"]["endpoint"]:
        return model.removeprefix("local-desk-")
    return next((key for key, value in PROFILES.items() if value["model"] == settings.get("model") and settings.get("endpoint") in {value["endpoint"], PROFILES["bonsai"]["endpoint"]}), "")

def inference_payload(settings, messages, mode="chat"):
    payload = {"model": settings["model"], "messages": messages, "temperature": settings["temperature"],
               "max_tokens": settings["max_tokens"], "stream": True, "stream_options": {"include_usage": True}}
    if not settings["thinking"]:
        payload["reasoning_effort"] = "none"
    if mode in {"edit", "extract"}:
        payload["response_format"] = {"type": "json_object"}
        if settings["endpoint"].endswith(":1235/v1"):
            payload["response_format"]["schema"] = EDIT_SCHEMA if mode == "edit" else EXTRACT_SCHEMA
    if profile_for(settings) == "bonsai":
        thinking = settings["thinking"]
        payload.update({"reasoning_effort": "medium" if thinking else "none", "top_p": 0.95 if thinking else 0.8,
                        "top_k": 20, "min_p": 0.05 if thinking else 0.0, "presence_penalty": 0.0 if thinking else 1.5})
        if thinking:
            # Reserve output for the final answer instead of spending it all on thought.
            payload["thinking_budget_tokens"] = min(1024, settings["max_tokens"] // 2)
    return payload

def validate_extracted(value):
    import math
    if not isinstance(value, dict) or set(value) != {"items"} or not isinstance(value["items"], list) or len(value["items"]) > 100:
        raise ValueError("項目の配列が不正です。")
    for item in value["items"]:
        if not isinstance(item, dict) or set(item) != {"name", "value", "source"}:
            raise ValueError("項目の形式が不正です。")
        if not isinstance(item["name"], str) or not isinstance(item["source"], str):
            raise ValueError("項目名と根拠は文字列にしてください。")
        data = item["value"]
        if data is not None and not isinstance(data, (str, int, float, bool)):
            raise ValueError("値の形式が不正です。")
        if isinstance(data, float) and not math.isfinite(data):
            raise ValueError("有限ではない数値は使用できません。")
    return value

class EngineManager:
    def __init__(self, base: Path, data: Path, *, owned_port=1235):
        if not isinstance(owned_port, int) or not 1024 <= owned_port <= 65535:
            raise ValueError("専用エンジンのポート番号が不正です。")
        self.owned_port = owned_port
        self.owned_endpoint = f"http://127.0.0.1:{owned_port}/v1"
        self.base, self.data = base, data
        self.runtime = base / ".runtime" / "bonsai"
        self.server = None
        self.log = None
        self.log_thread = None
        self.key_path = data / "bonsai-api-key"
        self.standalone = False
        self.shutdown_requested = threading.Event()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        from model_catalog import Catalog
        self.catalog = Catalog(data)

    def configs(self):
        return self.catalog.configs(PROFILES)

    def lms_path(self):
        candidate = Path.home() / ".lmstudio/bin/lms.exe"
        return str(candidate) if candidate.is_file() else shutil.which("lms")

    def profiles(self, settings):
        home = Path.home() / ".lmstudio/models"
        installed = {
            "bonsai": (self.runtime / "bin/llama-server.exe").is_file() and (self.runtime / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf").is_file() and (self.runtime / "manifest.json").is_file(),
            "qwen": bool(self.lms_path()) and (home / "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf").is_file(),
            "ornith": bool(self.lms_path()) and (home / "ornith-ai/Ornith-1.5-9B-GGUF/Ornith-1.5-9B-Q6_K.gguf").is_file(),
        }
        if self.standalone:
            runtime_ready = installed["bonsai"]
            installed = {key: runtime_ready and self.model_path(key).is_file() for key in self.configs()}
        else:
            for key in self.catalog.items:
                installed[key] = (self.runtime / "bin/llama-server.exe").is_file() and self.model_path(key).is_file()
        from model_catalog import INFORMATION
        return [{"id": key, "label": value["label"], "installed": installed[key] and os.name == "nt",
                 "active": key == profile_for(settings), "path":str(self.model_path(key)), "bytes":self.model_path(key).stat().st_size if self.model_path(key).is_file() else 0,
                 "context_length":value["context_length"], **INFORMATION.get(key,{}), **{name:self.catalog.items[key][name] for name in ("license","source") if key in self.catalog.items}}
                for key, value in self.configs().items()]

    def _run(self, arguments, timeout=120):
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("モデルの準備が時間内に完了しませんでした。再度切り替えを試してください。") from exc
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "Engine command failed")[-1200:])
        return result.stdout

    def _lms(self, *arguments):
        executable = self.lms_path()
        if not executable:
            raise RuntimeError("LM StudioのCLIが見つかりません。READMEの準備手順を確認してください。")
        return self._run([executable, *arguments])

    def _model_ids(self, endpoint):
        try:
            request = urllib.request.Request(endpoint + "/models", headers=self.headers_for(endpoint))
            with self.opener.open(request, timeout=2) as response:
                return [item["id"] for item in json.load(response).get("data", [])]
        except (OSError, ValueError, KeyError):
            return []

    def headers_for(self, endpoint):
        if endpoint == self.owned_endpoint and self.key_path.is_file():
            return {"Authorization": "Bearer " + self.key_path.read_text(encoding="ascii").strip()}
        return {}

    def model_path(self, profile):
        if profile in self.catalog.items:
            return Path(self.catalog.items[profile]["path"])
        if profile == "bonsai":
            return self.runtime / "models/Ternary-Bonsai-2-27B-PQ2_0.gguf"
        home = Path.home() / ".lmstudio/models"
        return home / ("lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf" if profile == "qwen" else "ornith-ai/Ornith-1.5-9B-GGUF/Ornith-1.5-9B-Q6_K.gguf")

    def _loaded(self):
        raw = self._lms("ps", "--json")
        return json.loads(raw)

    def _unload_managed_lms(self, keep=""):
        if not self.lms_path():
            return
        # The daemon may be stopped while Bonsai is used. Do not start it just to unload.
        if not self._model_ids("http://127.0.0.1:1234/v1"):
            return
        for item in self._loaded():
            identifier = item.get("identifier")
            if identifier in {"local-desk-qwen", "local-desk-ornith"} and identifier != keep:
                if item.get("status") not in {None, "idle"} or item.get("queued", 0):
                    raise RuntimeError("LM Studioのモデルが別の処理に使用されています。完了後に切り替えてください。")
                self._lms("unload", identifier)

    def _stop_owned(self):
        if self.server is not None:
            if self.server.poll() is None:
                self.server.terminate()
                try:
                    self.server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.server.kill()
                    self.server.wait(timeout=1)
            self.server = None
        if self.log is not None:
            if self.log_thread:
                self.log_thread.join(timeout=1)
            self.log.close()
            self.log = None

    @staticmethod
    def _pump_log(pipe, log):
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while chunk := pipe.read(4096):
                log.write(decoder.decode(chunk))
            log.write(decoder.decode(b"", final=True))
        finally:
            pipe.close()

    def activate(self, profile):
        if self.shutdown_requested.is_set():
            raise RuntimeError("アプリを終了しています。")
        if profile not in self.configs():
            raise ValueError("モデルのプリセットが不正です。")
        status = next(item for item in self.profiles({}) if item["id"] == profile)
        if not status["installed"]:
            raise RuntimeError("このモデルを準備できません。READMEの導入手順を確認してください。")
        config = dict(self.configs()[profile])
        if profile == "bonsai" or self.standalone or profile in self.catalog.items:
            config["endpoint"] = self.owned_endpoint
            if self.server is not None and self.server.poll() is None and config["model"] in self._model_ids(config["endpoint"]):
                if not self.standalone:
                    self._unload_managed_lms()
                return config
            self._stop_owned()
            # Refuse to launch over an unrelated service on our dedicated port.
            import socket
            with socket.socket() as connection:
                if connection.connect_ex(("127.0.0.1", self.owned_port)) == 0:
                    raise RuntimeError(f"ポート{self.owned_port}を別のサーバーが使用しています。")
            model = self.model_path(profile)
            manifest = json.loads((self.runtime / "manifest.json").read_text(encoding="utf-8"))
            entry = next((item for item in manifest["files"] if item["name"] == model.name), None)
            if profile == "bonsai" and (not entry or entry["size"] != model.stat().st_size):
                raise RuntimeError("Bonsaiのモデルファイルを再検証してください。python setup_bonsai.pyで復旧できます。")
            if not self.standalone:
                self._unload_managed_lms()
            self.data.mkdir(parents=True, exist_ok=True)
            if not self.key_path.is_file():
                self.key_path.write_text(secrets.token_urlsafe(32) + "\n", encoding="ascii")
            self.log = BoundedLog(self.data / ("engine-server.log" if self.standalone else "bonsai-server.log"))
            arguments = [str(self.runtime / "bin/llama-server.exe"), "-m", str(model), "--host", "127.0.0.1", "--port", str(self.owned_port),
                         "--alias", config["model"], "-ngl", "99", "-fa", "on", "-c", str(config["context_length"]), "-np", "1", "--jinja",
                         "--temp", "0.7", "--top-p", "0.8", "--top-k", "20", "--min-p", "0", "--reasoning-budget", "1024",
                         "--api-key-file", str(self.key_path), "--cors-origins", "http://127.0.0.1:8765", "--no-webui"]
            self.server = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.log_thread = threading.Thread(target=self._pump_log, args=(self.server.stdout, self.log), daemon=True)
            self.log_thread.start()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if self.shutdown_requested.is_set():
                    self._stop_owned()
                    raise RuntimeError("アプリを終了しています。")
                if self.server.poll() is not None:
                    self._stop_owned()
                    raise RuntimeError("モデルの起動に失敗しました。.data/内のserver.logを確認してください。")
                if config["model"] in self._model_ids(config["endpoint"]):
                    return config
                time.sleep(0.3)
            self._stop_owned()
            raise RuntimeError("Bonsaiのモデル読み込みが時間内に完了しませんでした。")
        self._stop_owned()
        self._lms("server", "start", "--port", "1234", "--bind", "127.0.0.1")
        self._unload_managed_lms(config["model"])
        loaded = self._loaded()
        current = next((item for item in loaded if item.get("identifier") == config["model"]), None)
        if not current:
            key = "qwen3.5-9b" if profile == "qwen" else "ornith-1.5-9b"
            self._lms("load", key, "--gpu", "max", "--context-length", str(config["context_length"]), "--parallel", "1", "--identifier", config["model"], "-y")
        elif current.get("contextLength"):
            config["context_length"] = int(current["contextLength"])
        return config

    def close(self):
        self.shutdown_requested.set()
        self._stop_owned()

    def request_shutdown(self):
        self.shutdown_requested.set()
        # Closing the process unblocks an HTTP stream waiting on prefill too.
        process = self.server
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
