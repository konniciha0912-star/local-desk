"""Experimental, explicitly requested editor completion; no history pollution."""
import threading
import uuid
from context import TokenCounter
from generation import stream

def complete(application,body):
    from app import AppError
    prefix,suffix,language=(body.get(key,"") for key in ("prefix","suffix","language"))
    if any(not isinstance(value,str) for value in [prefix,suffix,language]) or len(prefix)>8000 or len(suffix)>2000 or len(language)>30: raise AppError("補完の入力範囲が大きすぎます。")
    if not application.generation_lock.acquire(blocking=False): raise AppError("AIが使用中です。",409)
    identifier=str(body.get("request_id") or uuid.uuid4().hex)[:100];cancel=threading.Event()
    with application.active_lock:application.active[identifier]={"cancel":cancel}
    try:
        settings=application.settings_copy();settings.update(thinking=False,max_tokens=128,temperature=0.2)
        messages=[{"role":"system","content":"Complete the code at <cursor>. Return only the text to insert, no Markdown fences or explanations. Keep indentation. Do not repeat existing code. Use at most three lines."},
                  {"role":"user","content":f"Language: {language}\n{prefix}<cursor>{suffix}"}]
        if TokenCounter(application.engines,settings).count(messages)>settings["context_length"]-256: raise AppError("補完範囲を短くしてください。")
        output={"content":"","reasoning":"","usage":{},"finish_reason":""}
        stream(application,settings,messages,"chat",identifier,cancel,lambda text,thought:None,output)
        text=output["content"]
        if text.startswith("```"): text="\n".join(text.splitlines()[1:]).removesuffix("```").rstrip()
        return {"text":"" if cancel.is_set() else "\n".join(text.splitlines()[:3]),"cancelled":cancel.is_set(),"usage":output["usage"]}
    except (OSError,ValueError,RuntimeError) as error:
        if cancel.is_set():return {"text":"","cancelled":True}
        raise AppError(str(error)) from error
    finally:
        with application.active_lock:application.active.pop(identifier,None)
        application.generation_lock.release()
