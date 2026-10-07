"""Explicit local document/audio import in a bounded child process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave

MAX_INPUT = 50 * 1024 * 1024
VOICE_REPOSITORY = "Systran/faster-whisper-base"

def run_import(base, path, kind):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > MAX_INPUT:
        raise ValueError("50MB以下のファイルの絶対パスを選んでください。")
    suffixes = {"document": {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".bmp", ".webp"}, "audio": {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}}
    if kind not in suffixes or path.suffix.lower() not in suffixes[kind]: raise ValueError("対応するファイル形式を選んでください。")
    # stdout is capped by the worker's 20,000-character result. No model-generated command.
    process = subprocess.Popen([sys.executable,str(base / "import_worker.py"),kind,str(path),str(base / ".runtime/voice/base")], stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    job = None
    try:
        if os.name == "nt":
            from desktop import ProcessJob
            job = ProcessJob(process)
        try: stdout, stderr = process.communicate(timeout=90 if kind=="audio" else 60)
        except subprocess.TimeoutExpired:
            if job: job.close()
            process.kill(); process.communicate(timeout=5)
            raise ValueError("読み取りが時間内に完了しませんでした。小さい資料・短い音声で試してください。")
        if process.returncode: raise ValueError("読み取りに失敗しました。診断・依存の導入を確認し、別のファイルで試してください。")
        result=json.loads(stdout.decode("utf-8"))
        if result.get("error"): raise ValueError(result["error"])
        return result
    finally:
        if job: job.close()
        if process.poll() is None: process.kill(); process.wait(timeout=5)

class Recorder:
    """Only start after the microphone button, stop after at most 30 seconds."""
    def __init__(self,data):
        self.data=data; self.lock=threading.RLock(); self.stream=None; self.chunks=[]; self.timer=None

    def start(self):
        import sounddevice as sd
        with self.lock:
            if self.stream: raise ValueError("録音中です。文字起こしするか停止してください。")
            self.chunks=[]; self.started=time.monotonic(); self.problem=""
            def collect(data,frames,clock,status):
                with self.lock:
                    if status: self.problem="音声に欠落がある可能性があります。内容を確認してください。"
                    if len(self.chunks)<1000: self.chunks.append(bytes(data))
            self.stream=sd.RawInputStream(samplerate=16000,channels=1,dtype="int16",blocksize=800,callback=collect)
            try: self.stream.start()
            except Exception: self.stream.close(); self.stream=None; raise
            self.timer=threading.Timer(30,self.stop); self.timer.daemon=True; self.timer.start()
        return {"recording":True,"maximum_seconds":30}

    def stop(self,discard=False):
        with self.lock:
            stream,self.stream=self.stream,None
            if self.timer: self.timer.cancel(); self.timer=None
        # InputStream.stop waits for callbacks, so never hold their lock here.
        if stream: stream.stop(); stream.close()
        if discard:
            with self.lock: self.chunks=[]

    def finish(self):
        self.stop()
        with self.lock:
            raw=b"".join(self.chunks); self.chunks=[]
        if not raw: raise ValueError("録音された音声がありません。")
        path=self.data / "imports/microphone.wav"; path.parent.mkdir(exist_ok=True)
        with wave.open(str(path),"wb") as target:
            target.setnchannels(1);target.setsampwidth(2);target.setframerate(16000);target.writeframes(raw)
        return path
