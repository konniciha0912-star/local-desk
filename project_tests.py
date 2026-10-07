"""Only explicit, predefined project test runners; never model commands."""
from collections import deque
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

COMMANDS = {"unittest": ["-m", "unittest", "discover", "-s", "tests", "-v"], "pytest": ["-m", "pytest", "-q"]}


def run(root, kind, confirmed):
    from app import AppError
    directory = Path(root).resolve()
    if not confirmed or kind not in COMMANDS or not directory.is_dir():
        raise AppError("実行するプロジェクトとテストコマンドを確認してください。")
    arguments = [sys.executable, *COMMANDS[kind]]
    chunks, lock = deque(), threading.Lock()
    process = subprocess.Popen(arguments, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env={**os.environ,"PYTHONIOENCODING":"utf-8"},
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, start_new_session=os.name != "nt")
    job = None
    if os.name == "nt":
        from desktop import ProcessJob
        try: job = ProcessJob(process)
        except OSError:
            process.kill(); process.wait(); raise AppError("テストの終了管理を準備できませんでした。実行を中止しました。")
    def collect():
        while value := process.stdout.read(4096):
            with lock:
                chunks.append(value)
                while sum(map(len,chunks)) > 65536: chunks.popleft()
    reader = threading.Thread(target=collect,daemon=True); reader.start()
    started, timed_out = time.monotonic(), False
    try:
        process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        timed_out = True
        if job: job.close()
        else: os.killpg(process.pid,signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        if job: job.close()
        reader.join(timeout=2)
        process.stdout.close()
    with lock: output = b"".join(chunks).decode("utf-8",errors="replace")
    return {"command": "python " + " ".join(COMMANDS[kind]), "root": str(directory), "exit_code":process.returncode,
            "seconds":round(time.monotonic()-started,2),"timed_out":timed_out,"output":output,
            "output_limit":65536}
