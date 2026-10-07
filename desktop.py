"""Local Desk native window: one owner for the UI, HTTP server and AI process."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

import app
from engines import profile_for
from maintenance import BoundedLog

TITLE = "Local Desk"

def validate_geometry(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid window geometry")
    result = {"width": max(760, min(2560, int(value.get("width", 1180)))),
              "height": max(560, min(1600, int(value.get("height", 800)))),
              "maximized": bool(value.get("maximized", False)),
              "presentation": "companion" if value.get("presentation") == "companion" else "normal"}
    for key in ["x", "y"]:
        number = value.get(key)
        result[key] = int(number) if number is not None and -32768 <= int(number) <= 32768 else None
    return result

def visible_geometry(value, screens):
    result = validate_geometry(value)
    if result["x"] is not None and result["y"] is not None and screens:
        x, y = result["x"], result["y"]
        # Keep enough of the title bar visible to move the window. A disconnected
        # monitor must not make the application's only window inaccessible.
        if not any(screen.x <= x + result["width"] - 64 and x + 64 <= screen.x + screen.width
                   and screen.y <= y and y + 32 <= screen.y + screen.height for screen in screens):
            result["x"] = result["y"] = None
    return result


class SingleInstance:
    """Windows mutex stays held until the complete shutdown finishes."""
    def __init__(self, directory):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = "Local\\LocalDesk-" + hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:20]
        self.handle = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(error)
        self.first = error != 183

    def focus(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        for _ in range(20):
            bar = user.FindWindowW(None, "Local Desk — 上端バー")
            window = bar if bar and user.IsWindowVisible(bar) else user.FindWindowW(None, TITLE)
            if window:
                user.ShowWindow(window, 9)
                user.SetForegroundWindow(window)
                return
            time.sleep(0.1)

    def close(self):
        self.kernel.CloseHandle(self.handle)


class ProcessJob:
    """Kill descendants on an unexpected exit too (Windows Job Object)."""
    def __init__(self, process=None):
        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel, self.owner = kernel, process
        self.handle = kernel.CreateJobObjectW(None, None)
        limits = EXTENDED()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        target = int(process._handle) if process is not None else kernel.GetCurrentProcess()
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle, target):
            raise ctypes.WinError(ctypes.get_last_error())
        # Keep the handle open until process exit. Closing it would also kill this owner.

    def close(self):
        if self.owner is not None and self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def loading_page(message="AIを準備しています…"):
    return """<!doctype html><html lang="ja"><meta charset="utf-8"><style>
    body{margin:0;background:#f5f6f2;color:#28463e;font:16px 'Yu Gothic UI',sans-serif;display:grid;place-content:center;height:100vh;text-align:center}
    .logo{width:64px;height:64px;background:#235d50;color:white;border-radius:18px;font-size:40px;line-height:64px;margin:0 auto 24px}
    p{color:#63746e;line-height:1.8;max-width:560px}h1{font-size:28px}</style>
    <div class="logo">L</div><h1>Local Desk</h1><p>""" + html.escape(message) + "</p><p>このウィンドウを閉じるとAIも終了します。</p></html>"


class DesktopSession:
    def __init__(self, server, window, profile):
        self.server, self.window, self.profile = server, window, profile
        self.stopping = threading.Event()
        self.ready = threading.Event()
        self.worker = None
        self.http_thread = None
        self.companion = None
        self.shortcut = None

    def bind_drop(self):
        if self.stopping.is_set() or self.window.get_current_url() != f"http://127.0.0.1:{self.server.server_port}/":
            return
        from webview.dom import DOMEventHandler
        element = self.window.dom.get_element("#project-pane")
        if element:
            def dropped(event):
                files = event.get("dataTransfer", {}).get("files", [])
                paths = [item.get("pywebviewFullPath") for item in files if item.get("pywebviewFullPath")]
                if paths:
                    self.window.evaluate_js("window.dispatchEvent(new CustomEvent('local-desk-drop',{detail:" + json.dumps(paths) + "}))")
            element.on("drop", DOMEventHandler(dropped, prevent_default=True))

    def start(self):
        self.worker = threading.Thread(target=self._prepare, name="model-startup", daemon=True)
        self.worker.start()

    def _prepare(self):
        try:
            self.window.load_html(loading_page(self.server.application.engines.configs().get(self.profile,{}).get("label",self.profile) + "を読み込んでいます…"))
            self.server.application.switch_profile(self.profile)
            if self.stopping.is_set():
                return
            self._open_page()
            logging.info("DESKTOP_READY model=%s pid=%s", self.profile, os.getpid())
        except Exception as exc:
            if self.stopping.is_set():
                logging.info("Desktop startup cancelled by window close")
            else:
                logging.exception("Desktop startup failed")
                self.server.application.startup_error = str(exc)
                self._open_page()

    def _open_page(self):
        if self.stopping.is_set():
            return
        self.http_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        self.http_thread.start()
        self.ready.set()
        self.window.load_url(f"http://127.0.0.1:{self.server.server_port}")
        if self.companion:
            self.companion.bar.load_url(f"http://127.0.0.1:{self.server.server_port}/companion.html")
        if self.companion:
            from companion import Shortcut
            self.shortcut = Shortcut(lambda: self.companion.action("normal" if self.companion.mode == "companion" else "expand"))
            if not self.shortcut.start():
                self.server.application.notices.append("Ctrl+Alt+Spaceは他のアプリが使用中のため、上端バーのボタンを使ってください。")

    def request_stop(self):
        self.server.application.recorder.stop(discard=True)
        self.stopping.set()
        if self.shortcut:
            self.shortcut.close()
        self.server.application.cancel_active()
        self.server.application.engines.request_shutdown()
        connections = getattr(self.server.application, "connections", None)
        if connections:
            connections.close()

    def close(self):
        self.request_stop()
        # Unblock active generation before waiting for startup or a model switch.
        application = self.server.application
        with application.active_lock:
            active = list(application.active.values())
        for item in active:
            item["cancel"].set()
        if self.worker:
            self.worker.join(timeout=5)
        # A settings request may be loading another model. Its lifecycle must finish
        # before cleanup or it could launch a new engine after the window closed.
        acquired = application.generation_lock.acquire(timeout=3)
        try:
            application.engines.close()
        finally:
            if acquired:
                application.generation_lock.release()
        if self.http_thread:
            if self.http_thread.is_alive():
                self.server.shutdown()
            self.http_thread.join(timeout=5)
        self.server.server_close()
        logging.info("DESKTOP_CLOSED engines stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=app.BASE / ".data")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile", choices=("bonsai", "qwen", "ornith"))
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("このデスクトップ起動はWindows用です。ブラウザ版にはapp.pyを使ってください。")
    args.data_dir.mkdir(parents=True, exist_ok=True)
    # pythonw has no console streams; capture diagnostics locally instead.
    stream = BoundedLog(args.data_dir / "desktop.log")
    sys.stdout = sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream, format="%(asctime)s %(levelname)s %(message)s", force=True)
    instance = SingleInstance(args.data_dir)
    if not instance.first:
        instance.focus()
        instance.close()
        return 0
    session = None
    server = None
    try:
        job = ProcessJob()
        import webview
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["ALLOW_FILE_URLS"] = False
        from persistence import JsonDocument
        geometry = JsonDocument(args.data_dir / "window.json", {}, validate_geometry)
        saved = visible_geometry(geometry.load(), webview.screens)
        width = max(760, min(2560, int(saved.get("width", 1180))))
        height = max(560, min(1600, int(saved.get("height", 800))))
        window = webview.create_window(TITLE, html=loading_page(), width=width, height=height, min_size=(760, 560), text_select=True,
                                       x=saved.get("x"), y=saved.get("y"), maximized=bool(saved.get("maximized", False)))
        geometry_state = {"width": width, "height": height, "x": saved.get("x"), "y": saved.get("y"), "maximized": bool(saved.get("maximized", False)), "presentation": saved.get("presentation", "normal")}
        def resized(w, h):
            if not geometry_state["maximized"]:
                geometry_state.update(width=w, height=h)
        def moved(x, y):
            if not geometry_state["maximized"]:
                geometry_state.update(x=x, y=y)
        window.events.resized += resized
        window.events.moved += moved
        window.events.maximized += lambda: geometry_state.update(maximized=True)
        window.events.restored += lambda: geometry_state.update(maximized=False)
        window.events.closing += lambda: geometry.save(geometry_state)
        try:
            server = app.make_server(args.port, args.data_dir)
        except OSError:
            window.load_html(loading_page("以前のブラウザ版が起動中です。そちらのターミナルでCtrl+Cを押して終了してから、アイコンを開き直してください。"))
        if server:
            server.application.desktop_window = window
            server.application.engines.standalone = True
            profile = args.profile or profile_for(server.application.settings) or "bonsai"
            session = DesktopSession(server, window, profile)
            from companion import Companion, TITLE as BAR_TITLE, position
            x, y, w, h = position(webview.screens, saved, 320, 68)
            bar = webview.create_window(BAR_TITLE, html=loading_page("準備中"), width=w, height=h,
                                       min_size=(280, 64), x=x, y=y, hidden=True, frameless=True,
                                       easy_drag=False, resizable=False, on_top=True, focus=False,
                                       background_color="#162c26", text_select=True)
            controller = Companion(window, bar, session, geometry, geometry_state, webview.screens)
            server.application.desktop_controller = controller
            session.companion = controller
            window.events.minimized += lambda: setattr(controller, "minimized", True)
            bar.events.loaded += controller.loaded
            window.events.closing += lambda: controller.closing(window)
            bar.events.closing += lambda: controller.closing(bar)
            window.events.loaded += session.bind_drop
            window.events.closing += session.request_stop
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LocalDesk.Desktop")
        webview.start(session.start if session else None, gui="edgechromium", private_mode=False,
                      storage_path=str(args.data_dir / "webview"), icon=str(app.BASE / "web/local-desk.ico"))
    except Exception:
        logging.exception("Desktop fatal error")
        ctypes.windll.user32.MessageBoxW(None, "Local Deskを起動できませんでした。.data/desktop.logをご確認ください。", TITLE, 0x10)
        return 1
    finally:
        if session:
            session.close()
        elif server:
            server.server_close()
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
