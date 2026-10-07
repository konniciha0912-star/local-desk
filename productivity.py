"""Editable request templates stored privately and validated on every save."""
import uuid
import os
from persistence import JsonDocument

MODES = {"chat", "summary", "translate", "code", "edit", "extract"}
DEFAULT_TEMPLATES = [
    {"id": "mail", "name": "メールの下書き", "mode": "chat", "prompt": "次のメモから丁寧で簡潔なメールの下書きを作ってください。事実を補わず、足りない情報は【要確認】としてください。\n\n{input}"},
    {"id": "minutes", "name": "議事録", "mode": "summary", "prompt": "次の記録を議題・決定事項・担当者と期限・未解決事項に整理してください。記載がなければ不明としてください。\n\n{input}"},
    {"id": "proofread", "name": "誤字と表現の修正", "mode": "chat", "prompt": "次の文章の意味を変えずに誤字と不自然な表現を直してください。修正文と変更理由を示してください。\n\n{input}"},
    {"id": "daily", "name": "日報", "mode": "summary", "prompt": "次の作業メモを、実施したこと・結果・困ったこと・次の作業に分けて日報にしてください。書かれていない実績を加えないでください。\n\n{input}"},
    {"id": "logs", "name": "ログから問題を整理", "mode": "code", "prompt": "次のログの事実を時刻順に整理し、問題の候補・根拠・確認方法を示してください。原因の推測と確認済み事実を区別してください。\n\n{input}"},
    {"id": "extract", "name": "項目をJSONへ抽出", "mode": "extract", "prompt": "次の文章から日時・金額・担当者・作業・期限を抽出してください。不明な値はnullにしてください。\n\n{input}"},
]


def validate_templates(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("テンプレートは100個まで保存できます。")
    result, ids = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("テンプレートが不正です。")
        name, prompt, identifier = item.get("name"), item.get("prompt"), item.get("id")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or not isinstance(prompt, str) or not 1 <= len(prompt) <= 6000:
            raise ValueError("名前は60文字、依頼文は6000文字まで指定してください。")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 64 or identifier in ids or item.get("mode") not in MODES:
            raise ValueError("テンプレートのIDまたはモードが不正です。")
        ids.add(identifier)
        result.append({"id": identifier, "name": name.strip(), "prompt": prompt, "mode": item["mode"]})
    return result


class Templates:
    def __init__(self, data):
        self.document = JsonDocument(data / "templates.json", DEFAULT_TEMPLATES, validate_templates)
        self.items = self.document.load()

    def save(self, item):
        with self.document.lock:
            value = dict(item)
            value["id"] = value.get("id") or uuid.uuid4().hex
            items = [existing for existing in self.items if existing["id"] != value["id"]] + [value]
            self.items = self.document.save(items)
        return value

    def delete(self, identifier):
        with self.document.lock:
            self.items = self.document.save([item for item in self.items if item["id"] != identifier])
        return {"ok": True}


def clipboard_text():
    if os.name != "nt":
        raise ValueError("この環境では画面の貼り付け操作を使ってください。")
    import ctypes
    from ctypes import wintypes
    user, kernel = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = wintypes.LPVOID
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalSize.restype = ctypes.c_size_t
    if not user.OpenClipboard(None):
        raise ValueError("クリップボードを開けません。少し待ってもう一度試してください。")
    try:
        handle = user.GetClipboardData(13)
        if not handle:
            return ""
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise ValueError("クリップボードの文字を読み取れませんでした。")
        try:
            characters = min(kernel.GlobalSize(handle) // 2, 20001)
            text = ctypes.wstring_at(pointer, characters).split("\0", 1)[0]
            if len(text) > 20000:
                raise ValueError("クリップボードの文章は20,000文字までです。範囲を選んで貼り付けてください。")
            return text
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()
