"""Cancelable local inference, including time spent waiting for HTTP headers."""
import http.client
import io
import json
import socket
import time
import urllib.error
import urllib.parse
from engines import inference_payload

class CancelableReader(io.RawIOBase):
    def __init__(self,sock,cancel): self.sock,self.cancel=sock,cancel
    def readable(self): return True
    def readinto(self,buffer):
        deadline=time.monotonic()+180
        while not self.cancel.is_set():
            try: return self.sock.recv_into(buffer)
            except socket.timeout:
                if time.monotonic()>deadline: raise TimeoutError("モデルの応答が時間内に届きませんでした。")
        return 0

class ResponseSocket:
    def __init__(self,sock,cancel):self.sock,self.cancel=sock,cancel
    def makefile(self,mode):return io.BufferedReader(CancelableReader(self.sock,self.cancel))


def stream(application, settings, messages, mode, request_id, cancel, delta, output):
    endpoint = urllib.parse.urlsplit(settings["endpoint"])
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port or 80, timeout=180)
    response = None
    with application.active_lock:
        application.active[request_id]["connection"] = connection
    try:
        if cancel.is_set(): return
        payload = json.dumps(inference_payload(settings,messages,mode)).encode()
        connection.request("POST",endpoint.path.rstrip("/") + "/chat/completions",body=payload,
                           headers={"Content-Type":"application/json", **application.engines.headers_for(settings["endpoint"])})
        if cancel.is_set(): return
        # Windows can leave a timed recv asleep after shutdown(). Short timed reads
        # observe cancellation even when no HTTP status/header has arrived yet.
        connection.sock.settimeout(.25)
        response = http.client.HTTPResponse(ResponseSocket(connection.sock,cancel))
        with application.active_lock:
            application.active[request_id]["response"] = response
        response.begin()
        if response.status >= 400:
            from app import readable_engine_error
            details = response.read(2000).decode("utf-8",errors="replace")
            error = urllib.error.HTTPError(settings["endpoint"],response.status,response.reason,{},None)
            message = readable_engine_error(RuntimeError(details)) if any(word in details.lower() for word in ["memory","context"]) else readable_engine_error(error)
            raise RuntimeError(message)
        while not cancel.is_set():
            line = response.readline()
            if not line: break
            if not line.startswith(b"data:"): continue
            data = line[5:].strip()
            if data == b"[DONE]": break
            try: item = json.loads(data)
            except ValueError: continue
            if item.get("error"): raise RuntimeError("推論サーバー: " + str(item["error"]))
            if item.get("usage"): output["usage"] = item["usage"]
            for choice in item.get("choices",[]):
                if choice.get("finish_reason"): output["finish_reason"] = choice["finish_reason"]
                value = choice.get("delta",{})
                text, thought = value.get("content") or "", value.get("reasoning_content") or value.get("reasoning") or ""
                if text or thought:
                    output["content"] += text; output["reasoning"] += thought
                    delta(text,thought)
    finally:
        if response: response.close()
        connection.close()
        with application.active_lock:
            if request_id in application.active:
                application.active[request_id].pop("response",None)
                application.active[request_id].pop("connection",None)
