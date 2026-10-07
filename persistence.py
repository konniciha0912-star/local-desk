"""Atomic JSON documents with one validated recovery copy."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
import time
import uuid


class JsonDocument:
    def __init__(self, path: Path, default, validate=lambda value: value):
        self.path = path
        self.backup = path.with_name(path.name + ".bak")
        self.default = default
        self.validate = validate
        self.lock = threading.RLock()
        self.notices = []

    def _read(self, path):
        return self.validate(json.loads(path.read_text(encoding="utf-8-sig")))

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def load(self):
        with self.lock:
            if self.path.exists():
                try:
                    return self._read(self.path)
                except (ValueError, TypeError, OSError, OverflowError, KeyError):
                    preserved = self.path.with_name(self.path.name + f".corrupt-{uuid.uuid4().hex}")
                    self.path.replace(preserved)
                    self.notices.append(f"{self.path.name}を読み込めなかったため、元のファイルを保管しました。")
            if self.backup.exists():
                try:
                    value = self._read(self.backup)
                    self._write(value)
                    self.notices.append(f"{self.path.name}を前回の正常な保存内容から復旧しました。")
                    return value
                except (ValueError, TypeError, OSError, OverflowError, KeyError):
                    self.notices.append(f"{self.path.name}を初期設定で開きました。")
            return copy.deepcopy(self.default)

    def save(self, value):
        with self.lock:
            value = self.validate(copy.deepcopy(value))
            if self.path.exists():
                try:
                    previous = self._read(self.path)
                except (ValueError, TypeError, OSError, OverflowError, KeyError):
                    previous = None
                if previous is not None:
                    temporary = self.backup.with_name(self.backup.name + ".tmp")
                    temporary.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(self.backup)
            self._write(value)
            return value


PREFERENCE_KEYS = ("temperature", "max_tokens", "thinking")


def validate_preferences(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid model preferences")
    result = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 200 or not isinstance(item, dict):
            raise ValueError("Invalid model preference entry")
        if not isinstance(item.get("thinking"), bool):
            raise ValueError("Invalid thinking preference")
        temperature, tokens = float(item["temperature"]), int(item["max_tokens"])
        if not (0 <= temperature <= 2 and 128 <= tokens <= 4096):
            raise ValueError("Invalid model preference range")
        result[key] = {"temperature": temperature, "max_tokens": tokens, "thinking": item["thinking"]}
    return result
