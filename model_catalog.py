"""User-selected GGUF locations and explicit model licensing information."""
from pathlib import Path
import re
import uuid
from persistence import JsonDocument

INFORMATION = {
    "bonsai": {"source": "https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf", "license": "Apache-2.0", "use": "日本語の相談・要約・コード修正", "memory": "16 GB GPU向け。長い入力では使用量が増えます。"},
    "qwen": {"source": "https://huggingface.co/Qwen/Qwen3.5-9B", "license": "Apache-2.0", "use": "素早い会話・翻訳・コード説明", "memory": "Q4_K_M、約6 GBモデル。16 GB GPUで使用。"},
    "ornith": {"source": "https://huggingface.co/ornith-ai/Ornith-1.5-9B", "license": "MIT", "use": "文章・会話。用途ごとに回答を比較", "memory": "Q6_K、約8 GBモデル。16 GB GPUで使用。"},
}

def validate(value):
    if not isinstance(value, dict) or len(value) > 30: raise ValueError("モデル登録の形式が不正です。")
    result = {}
    for key, item in value.items():
        if not re.fullmatch(r"bonsai|qwen|ornith|custom-[0-9a-f]{12}", key) or not isinstance(item, dict): raise ValueError("モデルIDが不正です。")
        path = item.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).suffix.lower() != ".gguf" or len(path) > 2000: raise ValueError("GGUFの絶対パスを指定してください。")
        label = item.get("label", key)
        context = item.get("context_length", 8192)
        if not isinstance(label, str) or not 1 <= len(label) <= 80 or type(context) is not int or context not in {4096,8192,16384,32768}: raise ValueError("モデル名・読込コンテキストを確認してください。")
        license = item.get("license", "提供元の利用条件を確認してください")
        source = item.get("source", "")
        if not isinstance(license, str) or len(license)>500 or not isinstance(source,str) or len(source)>1000 or (source and not source.startswith("https://")): raise ValueError("モデルの利用条件・提供元を確認してください。")
        result[key] = {"path": path, "label": label, "context_length": context, "license": license, "source": source}
    return result

class Catalog:
    def __init__(self, data):
        self.document = JsonDocument(data / "models.json", {}, validate)
        self.items = self.document.load()

    def save(self, body, active=""):
        identifier = body.get("id") or "custom-" + uuid.uuid4().hex[:12]
        if identifier == active: raise ValueError("別のモデルに切り替えてから登録内容を変更してください。")
        updated = dict(self.items)
        if body.get("delete") is True:
            updated.pop(identifier, None)
        else:
            item = {key:body[key] for key in ("path","label","context_length","license","source") if key in body}
            clean = validate({identifier:item})[identifier]
            path = Path(clean["path"])
            if not path.is_file(): raise ValueError("モデルファイルが見つかりません。")
            with path.open("rb") as stream:
                if stream.read(4) != b"GGUF": raise ValueError("GGUF形式のファイルを選んでください。")
            updated[identifier] = clean
        self.items = self.document.save(updated)
        return {"id":identifier}

    def configs(self, defaults):
        result = {key:dict(value) for key,value in defaults.items()}
        for key,item in self.items.items():
            config = result.setdefault(key, {"model":"local-desk-"+key,"endpoint":"http://127.0.0.1:1235/v1","temperature":0.6,"max_tokens":1536,"thinking":False})
            config.update({name:item[name] for name in ("label","context_length")})
        return result
