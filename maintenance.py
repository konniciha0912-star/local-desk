"""Private backup, bounded logs, and diagnostics without chat contents."""
from __future__ import annotations

import io
from contextlib import closing
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import zipfile

from version import VERSION

MAX_ARCHIVE = 256 * 1024 * 1024
DATA_DOCUMENTS = {"settings.json", "model-preferences.json", "templates.json", "models.json", "window.json"}


class BoundedLog:
    """A text stream usable by print() and a subprocess output pump."""
    def __init__(self, path, limit=2 * 1024 * 1024, copies=3):
        self.handler = RotatingFileHandler(path, maxBytes=limit, backupCount=copies, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.lock = threading.RLock()
        self.encoding = "utf-8"

    def write(self, text):
        with self.lock:
            for line in str(text).splitlines():
                if line:
                    self.handler.emit(logging.LogRecord("local-desk", logging.INFO, "", 0, line[:16384], (), None))
        return len(text)

    def flush(self):
        self.handler.flush()

    def close(self):
        with self.lock:
            self.handler.close()

    def isatty(self):
        return False


def sanitize(value, data=None):
    text = json.dumps(value, ensure_ascii=False)
    for path in [str(Path.home()), str(data) if data else ""]:
        if path:
            text = text.replace(json.dumps(path)[1:-1], "<local>")
    text = re.sub(r'(?i)(bearer|api[_-]?key|token)\s*[:= ]\s*[A-Za-z0-9_-]{12,}', r'\1 <redacted>', text)
    return json.loads(text)


def storage(data, runtime):
    def size(path):
        total = 0
        if path.is_file():
            return path.stat().st_size
        if path.exists():
            for item in path.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        pass
        return total
    return {"data_bytes": size(data), "undo_bytes": size(data / "backups"),
            "archives_bytes": size(runtime / "downloads"), "runtime_bytes": size(runtime),
            "free_bytes": shutil.disk_usage(data).free, "undo_protected": True}


def diagnostics(application):
    settings = application.settings_copy()
    gpu = {"available": False}
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run([executable, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=4,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode == 0:
                fields = result.stdout.splitlines()[0].split(",")
                gpu = {"available": True, "name": fields[0].strip(), "total_mib": int(fields[1]),
                       "used_mib": int(fields[2]), "driver": fields[3].strip()}
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
    with application.store.connect() as db:
        rows = db.execute("SELECT metadata FROM messages WHERE role='assistant' ORDER BY created DESC LIMIT 30").fetchall()
    performance = []
    for row in rows:
        item = json.loads(row[0])
        seconds, tokens = item.get("seconds", 0), item.get("usage", {}).get("completion_tokens", 0)
        if seconds and tokens:
            performance.append({"model": item.get("model"), "seconds": seconds, "tokens": tokens,
                                "tokens_per_second": round(tokens / seconds, 2), "first_token_seconds": item.get("first_token_seconds")})
    return sanitize({"version": VERSION, "python": platform.python_version(), "platform": platform.system(),
                     "desktop": application.engines.standalone, "model": settings["model"],
                     "context_length": settings["context_length"], "connection": application.models(), "gpu": gpu,
                     "performance": performance, "storage": storage(application.store.directory, application.engines.runtime)}, application.store.directory)


def backup(application):
    """SQLite online snapshot + data required for undo, never auth or browser state."""
    data = application.store.directory
    if (data / "batch-journal.json").exists(): raise ValueError("中断された変更を復旧してからバックアップを作成してください。")
    output = data / "exports" / f"local-desk-{time.time_ns()}.zip"
    output.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="local-desk-backup-") as temporary:
        snapshot = Path(temporary) / "history.sqlite3"
        with application.store.connect() as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("manifest.json", json.dumps({"format": 1, "version": VERSION, "created": time.time(), "data_root": str(data.resolve())}))
            package.write(snapshot, "history.sqlite3")
            for name in sorted(DATA_DOCUMENTS):
                if (data / name).is_file():
                    package.write(data / name, name)
            folder = data / "backups"
            if folder.exists():
                for item in folder.iterdir():
                    if item.is_file() and not item.is_symlink() and re.fullmatch(r"[a-f0-9]{32}\.bak", item.name):
                        package.write(item, "backups/" + item.name)
    return {"name": output.name, "bytes": output.stat().st_size,
            "warning": "会話と変更前のコードを含む個人用バックアップです。公開しないでください。"}


def restore(application, archive):
    """Validate everything before replacing; keep a complete pre-restore archive."""
    data = application.store.directory
    if (data / "batch-journal.json").exists(): raise ValueError("中断された変更を復旧してからバックアップを復元してください。")
    if not archive.is_file() or archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("バックアップが見つからないか、256MBを超えています。")
    with tempfile.TemporaryDirectory(prefix="local-desk-restore-") as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(archive) as package:
            names = package.namelist()
            if len(set(names)) != len(names) or len(names) > 10000:
                raise ValueError("バックアップの項目が不正です。")
            total = 0
            for item in package.infolist():
                name = item.filename
                if name not in DATA_DOCUMENTS | {"manifest.json", "history.sqlite3"} and not re.fullmatch(r"backups/[a-f0-9]{32}\.bak", name):
                    raise ValueError("バックアップに許可されていないパスがあります。")
                total += item.file_size
                if total > MAX_ARCHIVE or item.file_size > MAX_ARCHIVE:
                    raise ValueError("展開後のサイズが256MBを超えています。")
                target = stage / name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(package.read(item))
        manifest = json.loads((stage / "manifest.json").read_text())
        if manifest.get("format") != 1 or not (stage / "history.sqlite3").is_file():
            raise ValueError("対応するバックアップ形式ではありません。")
        # Reuse each document's own validator; unrecognized settings are never imported.
        from app import settings_document_value
        from persistence import validate_preferences
        for name in DATA_DOCUMENTS:
            if (stage / name).exists():
                value = json.loads((stage / name).read_text(encoding="utf-8"))
                if name == "settings.json":
                    settings_document_value(value)
                elif name == "model-preferences.json":
                    validate_preferences(value)
                elif name == "templates.json":
                    from productivity import validate_templates
                    validate_templates(value)
                elif name == "models.json":
                    from model_catalog import validate
                    validate(value)
        snapshot = stage / "history.sqlite3"
        with closing(sqlite3.connect(snapshot)) as db, db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("履歴の整合性検証に失敗しました。")
            for table in ["conversations", "messages", "changes"]:
                db.execute(f"SELECT * FROM {table} LIMIT 1")
            for row in db.execute("SELECT id,backup,before_hash,undone FROM changes").fetchall():
                identifier, old_path, expected, undone = row
                import hashlib
                candidate = stage / "backups" / (identifier + ".bak")
                if not undone and (not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected):
                    raise ValueError("取り消しに必要なバックアップが不足しています。")
                db.execute("UPDATE changes SET backup=? WHERE id=?", (str(data / "backups" / (identifier + ".bak")), identifier))
        retained = backup(application)
        # Incoming database owns only data files, not project source. Existing extra
        # undo copies remain protected. Roll back all replaced files on I/O failure.
        incoming = [item for item in stage.rglob("*") if item.is_file() and item.name != "manifest.json"]
        old = {}
        installed = []
        try:
            for item in incoming:
                target = data / item.relative_to(stage)
                old[target] = target.read_bytes() if target.exists() else None
                target.parent.mkdir(exist_ok=True)
                pending = target.with_name(target.name + ".restore-tmp")
                pending.write_bytes(item.read_bytes())
                pending.replace(target)
                installed.append(target)
        except Exception:
            for target in reversed(installed):
                if old[target] is None:
                    target.unlink(missing_ok=True)
                else:
                    target.write_bytes(old[target])
            raise
    from app import Store
    application.store = Store(data)
    application.settings = application.settings_document.load()
    application.model_preferences = application.preferences_document.load()
    application.templates.items = application.templates.document.load()
    application.engines.catalog.items = application.engines.catalog.document.load()
    return {"ok": True, "retained": retained["name"], "restart_required": True,
            "message": "復元しました。読込中のAIに設定を適用するため、アプリを開き直してください。"}
