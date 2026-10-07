"""Measure the loaded model's formatted prompt, with an explicit safe fallback."""
from __future__ import annotations
import hashlib
import json
import urllib.request


class TokenCounter:
    def __init__(self, engines, settings):
        self.engines, self.settings = engines, settings
        self.exact = bool(engines.standalone or settings["endpoint"].endswith(":1235/v1"))
        self.cache = {}

    def post(self, route, payload):
        endpoint = self.settings["endpoint"].removesuffix("/v1")
        request = urllib.request.Request(endpoint + route, method="POST", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json", **self.engines.headers_for(self.settings["endpoint"])})
        with self.engines.opener.open(request, timeout=3) as response:
            return json.load(response)

    def count(self, messages):
        key = json.dumps(messages, ensure_ascii=False)
        if key in self.cache:
            return self.cache[key]
        if self.exact:
            try:
                prompt = self.post("/apply-template", {"messages": messages, "chat_template_kwargs": {"enable_thinking": self.settings["thinking"]},
                                                       "reasoning_effort": "medium" if self.settings["thinking"] else "none"})["prompt"]
                value = len(self.post("/tokenize", {"content": prompt, "add_special": False, "parse_special": True})["tokens"])
            except (OSError, ValueError, KeyError, TypeError):
                self.exact = False
                self.cache.clear()
                return self.count(messages)
        else:
            # One token per UTF-8 byte plus framing overhead: intentionally conservative.
            value = sum(len(item["content"].encode("utf-8")) + 32 for item in messages)
        self.cache[key] = value
        return value


def build_context(settings, system, prompt, records, history, body, engines):
    counter = TokenCounter(engines, settings)
    budget = settings["context_length"] - settings["max_tokens"] - 128
    blocks, sources = [], []
    ranges = body.get("ranges", {})
    if not isinstance(ranges, dict):
        raise ValueError("参照範囲が不正です。")
    cursor = len(prompt)
    for record in records:
        lines = record["text"].splitlines(keepends=True)
        span = ranges.get(record["path"], {})
        if not isinstance(span, dict):
            raise ValueError("参照範囲が不正です。")
        start, end = int(span.get("start", 1)), int(span.get("end", len(lines) or 1))
        if not 1 <= start <= end <= max(1, len(lines)):
            raise ValueError("参照行はファイル内の範囲を指定してください。")
        header = f"\n\n--- FILE: {record['path']} " + ("[承認した新規作成先]" if record.get("create") else f"[L{start}-L{end}]") + " ---\n"
        text = "".join(lines[start - 1:end])
        block = header + text + "\n--- END FILE ---\n"
        sources.append({"path": record["path"], "start": start, "end": end, "total_lines": len(lines),
                        "offset": cursor + len(header), "characters": len(text), "sha256": record["sha256"], "create": bool(record.get("create"))})
        cursor += len(block)
        blocks.append(block)
    current = prompt + "".join(blocks)
    def messages_for(value, kept=None):
        return [{"role": "system", "content": system}] + (kept or []) + [{"role": "user", "content": value}]
    if counter.count(messages_for("")) > budget:
        raise ValueError("基本の指示または会話要約だけで入力枠を超えています。指示を短くするか、新しい会話で試してください。")
    clipped = False
    if counter.count(messages_for(current)) > budget:
        clipped = True
        low, high = 0, len(current)
        while low < high:
            middle = (low + high + 1) // 2
            if counter.count(messages_for(current[:middle] + "\n[入力の末尾を省略]")) <= budget:
                low = middle
            else:
                high = middle - 1
        current = current[:low] + "\n[入力の末尾を省略]"
        limit = low
    else:
        limit = len(current)
    kept, kept_ids = [], []
    requested = max(0, min(100, int(body.get("history_limit", 30))))
    for item in reversed(history[-requested:] if requested else []):
        candidate = [{"role": item["role"], "content": item["content"]}] + kept
        if counter.count(messages_for(current, candidate)) > budget:
            break
        kept, kept_ids = candidate, [item["id"]] + kept_ids
    while kept and kept[0]["role"] != "user":
        kept.pop(0); kept_ids.pop(0)
    for source in sources:
        count = max(0, min(source["characters"], limit - source.pop("offset")))
        source["included_characters"] = count
        source["clipped"] = count < source["characters"]
        if count:
            lines = next(record["text"].splitlines(keepends=True) for record in records if record["path"] == source["path"])
            fragment = "".join(lines[source["start"] - 1:source["end"]])[:count]
            source["included_end"] = source["start"] + len(fragment.splitlines()) - 1
            source["last_line_partial"] = count < source["characters"] and not fragment.endswith(("\n", "\r"))
        else:
            source["included_end"] = None
    messages = messages_for(current, kept)
    if counter.count(messages) > budget:
        raise ValueError("入力枠の計測中に接続が変わりました。モデルを準備してもう一度プレビューしてください。")
    scope = {"input_tokens": counter.count(messages), "measurement": "loaded-model" if counter.exact else "utf8-upper-bound",
             "budget": budget, "output_reserved": settings["max_tokens"], "history_used": len(kept), "history_total": len(history),
             "history_ids": kept_ids, "prompt_characters": len(prompt), "prompt_included": min(len(prompt), limit),
             "sources": sources, "clipped": clipped, "messages": messages}
    fingerprint = {"messages": messages, "sources": sources, "settings": settings}
    scope["digest"] = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return messages, scope
