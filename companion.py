"""Two native views, one application owner. No duplicate inference process."""

import json
import logging
import threading
import time

TITLE = "Local Desk — 上端バー"


def position(screens, geometry, width, height):
    screen = next(
        (
            s
            for s in screens
            if geometry.get("x") is not None
            and s.x <= geometry["x"] < s.x + s.width
            and s.y <= (geometry.get("y") or 0) < s.y + s.height
        ),
        screens[0] if screens else None,
    )
    if not screen:
        return None, None, width, height
    width, height = min(width, screen.width), min(height, screen.height - 32)
    return screen.x + (screen.width - width) // 2, screen.y + 12, width, height


class Companion:
    def __init__(self, main, bar, session, document, geometry, screens):
        self.main, self.bar, self.session = main, bar, session
        self.document, self.geometry, self.screens = document, geometry, screens
        self.lock = threading.RLock()
        self.closed = False
        self.expanded = False
        self.minimized = False
        self.mode = geometry.get("presentation", "normal")

    def save(self):
        self.geometry["presentation"] = self.mode
        self.document.save(self.geometry)

    def loaded(self):
        if self.closed or not self.session.ready.is_set():
            return
        if self.session.server.application.startup_error:
            self.action("normal")
        elif self.mode == "companion":
            self.action("companion")

    def action(self, action):
        with self.lock:
            if self.closed:
                raise ValueError("Local Deskは終了中です。")
            if action == "quit":
                # Do not destroy the request's WebView before its HTTP reply is sent.
                threading.Timer(0.2, self.shutdown).start()
            elif action in {"companion", "expand", "collapse"}:
                self.expanded = action == "expand"
                x, y, w, h = position(
                    self.screens,
                    self.geometry,
                    460 if self.expanded else 320,
                    460 if self.expanded else 68,
                )
                self.bar.resize(w, h)
                if x is not None:
                    self.bar.move(x, y)
                self.bar.show()
                self.main.hide()
                self.mode = "companion"
                self.save()
            elif action in {"normal", "conversation"}:
                self.main.show()
                if self.minimized:
                    self.main.restore()
                    self.minimized = False
                self.bar.hide()
                self.mode = "normal"
                self.save()
                if action == "conversation":
                    application = self.session.server.application
                    with application.active_lock:
                        conversation = application.last_activity.get("conversation")
                    if conversation:
                        self.main.evaluate_js(
                            "window.dispatchEvent(new CustomEvent('local-desk-conversation',{detail:"
                            + json.dumps(conversation)
                            + "}))"
                        )
            else:
                raise ValueError("表示操作が不正です。")
            return {"mode": self.mode, "expanded": self.expanded}

    def closing(self, source):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.save()
            except OSError:
                logging.exception("Could not save window mode during shutdown")
            self.session.request_stop()
        other = self.bar if source is self.main else self.main
        # A second WebView may still be initializing. Its destroy decorator waits
        # for shown; doing that inside FormClosing blocks the GUI which must show
        # it. Let the closing event return before disposing the other view.
        thread = threading.Thread(target=other.destroy, name="close-other-view", daemon=True)
        thread.start()

    def shutdown(self):
        with self.lock:
            if self.closed:
                return
        self.main.destroy()


class Shortcut:
    """Optional Ctrl+Alt+Space. Never replace an existing system registration."""

    def __init__(self, callback):
        self.callback = callback
        self.thread_id = None
        self.registered = False
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, name="companion-shortcut", daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(1)
        return self.registered

    def run(self):
        import ctypes
        from ctypes import wintypes

        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel.GetCurrentThreadId()
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.registered = bool(user.RegisterHotKey(None, 0x4C44, 0x4003, 0x20))
        self.ready.set()
        if not self.registered:
            return
        try:
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:
                    try:
                        self.callback()
                    except ValueError:
                        pass
        finally:
            user.UnregisterHotKey(None, 0x4C44)
            self.registered = False

    def close(self):
        if self.thread_id and self.registered:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(1)


def activity(application):
    with application.active_lock:
        active = [dict(item) for item in application.active.values()]
        last = dict(application.last_activity)
    item = active[0] if active else {}
    stage = item.get("stage")
    if not stage:
        stage = (
            "モデル・処理の準備中"
            if application.generation_lock.locked()
            else "資料を取得中" if application.import_lock.locked() else "待機中"
        )
    if application.startup_error:
        stage = "モデルの準備に失敗 — 通常画面で復旧"
    return {
        "busy": bool(active) or application.generation_lock.locked(),
        "cancellable": bool(active),
        "stage": stage,
        "model": application.settings_copy()["model"],
        "seconds": round(time.monotonic() - item["started"], 1) if item.get("started") else None,
        "last": last,
        "desktop": application.desktop_controller is not None,
    }
