"""Explicit service requests. Credentials stay encrypted, outside exports/history."""

from __future__ import annotations
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse as url
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from email.message import EmailMessage

SERVICES = {
    "github": "GitHub",
    "gmail": "Gmail",
    "calendar": "Googleカレンダー",
    "drive": "Google Drive",
    "youtube": "YouTube",
    "notion": "Notion",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "openai": "OpenAI",
    "gemini": "Gemini",
    "anthropic": "Claude",
}
GOOGLE = {"gmail", "calendar", "drive", "youtube"}
SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.readonly",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "drive": "https://www.googleapis.com/auth/drive.metadata.readonly",
    "youtube": "https://www.googleapis.com/auth/youtube.readonly",
}
HOSTS = {
    "api.github.com",
    "oauth2.googleapis.com",
    "gmail.googleapis.com",
    "www.googleapis.com",
    "api.notion.com",
    "graph.instagram.com",
    "open.tiktokapis.com",
    "www.youtube.com",
    "www.tiktok.com",
    "api.openai.com",
    "generativelanguage.googleapis.com",
    "api.anthropic.com",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("サービスからの転送は受け付けません。")


def remote(address, *, token=None, body=None, headers=None, form=None):
    parsed = url.urlsplit(address)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in HOSTS
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
    ):
        raise ValueError("対応サービスのHTTPS APIだけを利用できます。")
    fields = {"User-Agent": "LocalDesk/2.1", "Accept": "application/json", **(headers or {})}
    if token:
        fields["Authorization"] = "Bearer " + token
    data = None
    if form is not None:
        data = url.urlencode(form).encode()
        fields["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        fields["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(
            urllib.request.Request(address, data=data, headers=fields), timeout=25
        ) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("取得結果が大きすぎます。条件を絞ってください。")
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        code = exc.code
        exc.close()
        message = (
            "時間を置いて再試行してください。"
            if code >= 500
            else "権限・期限・利用枠を確認してください。"
        )
        raise ValueError(f"サービスAPIがエラーを返しました ({code})。" + message) from None
    except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError):
        raise ValueError(
            "サービスに接続できませんでした。ネットワークと設定を確認してください。"
        ) from None


def dpapi(raw, decrypt=False):
    if os.name != "nt":
        raise ValueError("認証情報の永続保存はWindows版で利用できます。")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    call = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    if not call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("Windowsの認証情報ストアを利用できませんでした。")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, wintypes.HLOCAL))


class Vault:
    def __init__(self, directory):
        self.path = Path(directory) / "connections.enc"
        self.lock = threading.RLock()

    def load(self):
        with self.lock:
            if not self.path.exists():
                return {}
            try:
                return json.loads(dpapi(self.path.read_bytes(), decrypt=True))
            except (OSError, ValueError):
                raise ValueError(
                    "接続情報を復号できません。同じWindowsユーザーで開いてください。"
                ) from None

    def update(self, key, value):
        with self.lock:
            values = self.load()
            if value is None:
                values.pop(key, None)
            else:
                values[key] = value
            encrypted = dpapi(json.dumps(values).encode())
            pending = self.path.with_suffix(".enc.tmp")
            pending.write_bytes(encrypted)
            pending.replace(self.path)


class Connections:
    def __init__(self, directory):
        self.vault = Vault(directory)
        self.lock = threading.RLock()
        self.pending = None
        self.oauth_server = None
        self.oauth_thread = None
        self.oauth_error = ""
        self.previews = {}
        self.closed = False

    def status(self):
        try:
            stored = self.vault.load()
            error = ""
        except ValueError as exc:
            stored = {}
            error = str(exc)
        google = stored.get("google", {})
        scopes = set(google.get("scope", "").split())
        return {
            "services": [
                {
                    "id": key,
                    "name": name,
                    "connected": (
                        bool(google.get("refresh_token") and SCOPES[key] in scopes)
                        if key in GOOGLE
                        else bool(stored.get(key, {}).get("token"))
                    ),
                    "configured": (
                        bool(stored.get("google-client"))
                        if key in GOOGLE
                        else bool(stored.get(key))
                    ),
                    "account": (
                        google.get("account", "")
                        if key in GOOGLE
                        else stored.get(key, {}).get("account", "")
                    ),
                    "model": stored.get(key, {}).get("model", ""),
                }
                for key, name in SERVICES.items()
            ],
            "oauth_pending": bool(self.pending),
            "error": error or self.oauth_error,
            "encrypted": os.name == "nt",
        }

    def close(self):
        # OAuth token exchange may be waiting for the network under self.lock.
        # Closing the UI must never wait for that request's timeout.
        self.closed = True
        self.pending = None
        server, self.oauth_server = self.oauth_server, None
        self.previews.clear()
        if server:
            server.shutdown()
            server.server_close()

    def _google_token(self):
        with self.lock:
            values = self.vault.load()
            value = values.get("google", {})
            if not value.get("refresh_token"):
                raise ValueError("先にGoogleアカウントを接続してください。")
            if value.get("expires_at", 0) < time.time() + 30:
                client = values.get("google-client", {})
                fresh = remote(
                    "https://oauth2.googleapis.com/token",
                    form={
                        "grant_type": "refresh_token",
                        "refresh_token": value["refresh_token"],
                        "client_id": client["client_id"],
                        "client_secret": client["client_secret"],
                    },
                )
                value.update(
                    access_token=fresh["access_token"],
                    expires_at=time.time() + int(fresh.get("expires_in", 3600)),
                )
                if not self.closed:
                    self.vault.update("google", value)
            return value["access_token"]

    def _oauth_start(self, body):
        with self.lock:
            if self.closed:
                raise ValueError("終了中です。")
            if self.pending:
                raise ValueError("Google認証が進行中です。完了またはキャンセルしてください。")
            client = self.vault.load().get("google-client")
            if not client:
                raise ValueError(
                    "Google Cloudのデスクトップ用OAuthクライアントJSONを先に登録してください。"
                )
            selected = body.get("services", list(GOOGLE))
            if (
                not isinstance(selected, list)
                or not selected
                or any(s not in GOOGLE for s in selected)
            ):
                raise ValueError("接続するGoogleサービスを選択してください。")
            scopes = {SCOPES[s] for s in selected}
            if body.get("drafts") is True:
                scopes.add("https://www.googleapis.com/auth/gmail.compose")
            owner = self

            class Callback(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    parsed = url.urlsplit(self.path)
                    query = url.parse_qs(parsed.query)
                    try:
                        if (
                            parsed.path != "/callback"
                            or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                        ):
                            raise ValueError("不正な認証応答です。")
                        owner._oauth_finish(query)
                        text = "接続しました。Local Deskへ戻ってください。"
                        status = 200
                    except (ValueError, OSError) as exc:
                        text = str(exc)
                        status = 400
                    raw = text.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            server = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
            server.daemon_threads = True
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            redirect = f"http://127.0.0.1:{server.server_port}/callback"
            self.pending = {
                "state": state,
                "verifier": verifier,
                "redirect": redirect,
                "expires": time.time() + 300,
                "client": client,
            }
            self.oauth_server = server
            self.oauth_error = ""
            self.oauth_thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
            )
            self.oauth_thread.start()
            timer = threading.Timer(300, self._stop_oauth, args=(server,))
            timer.daemon = True
            timer.start()
            params = {
                "client_id": client["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(sorted(scopes)),
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "code_challenge": base64.urlsafe_b64encode(
                    hashlib.sha256(verifier.encode()).digest()
                )
                .decode()
                .rstrip("="),
                "code_challenge_method": "S256",
            }
            return {
                "url": "https://accounts.google.com/o/oauth2/v2/auth?" + url.urlencode(params),
                "expires_in": 300,
            }

    def _stop_oauth(self, server):
        if server is None:
            return
        with self.lock:
            if self.oauth_server is server:
                self.oauth_server = None
                self.pending = None
        server.shutdown()
        server.server_close()

    def _oauth_finish(self, query):
        with self.lock:
            pending = self.pending
            if (
                not pending
                or pending["expires"] < time.time()
                or not secrets.compare_digest(query.get("state", [""])[0], pending["state"])
            ):
                raise ValueError("認証応答の確認に失敗しました。もう一度接続してください。")
            self.pending = None
            cleanup = threading.Timer(0.3, self._stop_oauth, args=(self.oauth_server,))
            cleanup.daemon = True
            cleanup.start()
            if query.get("error"):
                raise ValueError("Google認証がキャンセルされました。")
            code = query.get("code", [""])[0]
            if not code or len(code) > 4096:
                raise ValueError("認証コードがありません。")
            token = remote(
                "https://oauth2.googleapis.com/token",
                form={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": pending["client"]["client_id"],
                    "client_secret": pending["client"]["client_secret"],
                    "redirect_uri": pending["redirect"],
                    "code_verifier": pending["verifier"],
                },
            )
            if not token.get("refresh_token"):
                raise ValueError(
                    "更新トークンを取得できませんでした。Google側の許可を解除し、再接続してください。"
                )
            token["expires_at"] = time.time() + int(token.get("expires_in", 3600))
            if not self.closed:
                self.vault.update("google", token)

    def action(self, action, body):
        if self.closed:
            raise ValueError("アプリは終了中です。")
        service = body.get("service")
        if action == "google-config":
            try:
                parsed = json.loads(str(body.get("json", "")))
                client = parsed["installed"]
                clean = {"client_id": client["client_id"], "client_secret": client["client_secret"]}
                if (
                    not re.fullmatch(
                        r"[A-Za-z0-9._-]+\.apps\.googleusercontent\.com", clean["client_id"]
                    )
                    or not isinstance(clean["client_secret"], str)
                    or not 8 <= len(clean["client_secret"]) <= 512
                ):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise ValueError(
                    "Googleの『デスクトップアプリ』用OAuth JSONを貼り付けてください。"
                ) from None
            self.vault.update("google-client", clean)
            return {"saved": True}
        if action == "oauth-start":
            return self._oauth_start(body)
        if action == "oauth-cancel":
            with self.lock:
                self.pending = None
                server, self.oauth_server = self.oauth_server, None
            if server:
                server.shutdown()
                server.server_close()
            return {"cancelled": True}
        if action == "disconnect":
            if service not in SERVICES:
                raise ValueError("サービスが不正です。")
            self.vault.update("google" if service in GOOGLE else service, None)
            return {
                "disconnected": True,
                "note": "このPCの接続情報を削除しました。Googleサービスは共通認証のため一緒に解除されます。サービス側の許可解除はアカウント設定から行ってください。",
            }
        if action == "save":
            if service not in set(SERVICES) - GOOGLE:
                raise ValueError("サービスが不正です。")
            token = str(body.get("token", "")).strip()
            if not 8 <= len(token) <= 8192 or any(ord(c) < 33 for c in token):
                raise ValueError("APIキー・アクセストークンを確認してください。")
            account = "APIキー登録済み（未検証）"
            if service == "github":
                account = remote("https://api.github.com/user", token=token)["login"]
            if service == "notion":
                account = remote(
                    "https://api.notion.com/v1/users/me",
                    token=token,
                    headers={"Notion-Version": "2022-06-28"},
                ).get("name", "Notion")
            model = str(body.get("model", ""))[:100]
            if service in {"openai", "gemini", "anthropic"} and not re.fullmatch(
                r"[A-Za-z0-9._-]{1,100}", model
            ):
                raise ValueError("提供元のモデルIDを入力してください。")
            self.vault.update(service, {"token": token, "account": account, "model": model})
            return {"saved": True, "account": account}
        if action == "video":
            return self.video(body)
        if action == "preview":
            return self.preview(body)
        if action == "commit":
            return self.commit(body)
        if action == "ai":
            return self.ai(body)
        if action == "query":
            if body.get("confirmed") is not True:
                raise ValueError("検索条件を外部サービスへ送る操作を確認してください。")
            return self.query(service, str(body.get("query", ""))[:500])
        raise ValueError("連携操作が不正です。")

    def query(self, service, query):
        values = self.vault.load()
        token = self._google_token() if service in GOOGLE else values.get(service, {}).get("token")
        if not token:
            raise ValueError("先にサービスを接続してください。")
        quote = lambda x: url.quote(x, safe="")
        items = []
        if service == "github":
            if query:
                data = remote(
                    "https://api.github.com/search/issues?q=" + quote(query) + "&per_page=10",
                    token=token,
                )
                items = [
                    {
                        "title": i["title"],
                        "url": i["html_url"],
                        "text": str(i.get("body") or "")[:5000],
                    }
                    for i in data.get("items", [])
                ]
            else:
                data = remote(
                    "https://api.github.com/user/repos?sort=updated&per_page=10", token=token
                )
                items = [
                    {
                        "title": i["full_name"],
                        "url": i["html_url"],
                        "text": i.get("description") or "",
                    }
                    for i in data
                ]
        elif service == "gmail":
            data = remote(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=10&q="
                + quote(query or "in:inbox"),
                token=token,
            )
            for message in data.get("messages", []):
                m = remote(
                    "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                    + quote(message["id"])
                    + "?format=full",
                    token=token,
                )
                headers = {
                    h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])
                }
                items.append(
                    {
                        "title": headers.get("subject", "件名なし"),
                        "url": "https://mail.google.com/mail/u/0/#all/" + m["id"],
                        "text": "From: "
                        + headers.get("from", "")
                        + "\n"
                        + headers.get("date", "")
                        + "\n"
                        + gmail_text(m.get("payload", {}))[:8000],
                    }
                )
        elif service == "calendar":
            params = url.urlencode(
                {
                    "maxResults": 10,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "timeMin": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    **({"q": query} if query else {}),
                }
            )
            data = remote(
                "https://www.googleapis.com/calendar/v3/calendars/primary/events?" + params,
                token=token,
            )
            items = [
                {
                    "title": i.get("summary", "予定"),
                    "url": i.get("htmlLink", ""),
                    "text": json.dumps(
                        {
                            "start": i.get("start"),
                            "end": i.get("end"),
                            "description": i.get("description", ""),
                        },
                        ensure_ascii=False,
                    ),
                }
                for i in data.get("items", [])
            ]
        elif service == "drive":
            # Metadata only; do not imply that file bodies were read.
            escaped = query.replace("\\", "\\\\").replace("'", "\\'")
            params = url.urlencode(
                {
                    "pageSize": 10,
                    "fields": "files(id,name,mimeType,webViewLink,description)",
                    "q": "trashed = false" + (f" and name contains '{escaped}'" if query else ""),
                }
            )
            data = remote("https://www.googleapis.com/drive/v3/files?" + params, token=token)
            items = [
                {
                    "title": i["name"],
                    "url": i.get("webViewLink", ""),
                    "text": "ファイル情報（本文未取得）: "
                    + i.get("mimeType", "")
                    + "\n"
                    + i.get("description", ""),
                }
                for i in data.get("files", [])
            ]
        elif service == "youtube":
            if query:
                data = remote(
                    "https://www.googleapis.com/youtube/v3/search?"
                    + url.urlencode(
                        {"part": "snippet", "q": query, "type": "video", "maxResults": 10}
                    ),
                    token=token,
                )
                items = [
                    {
                        "title": i["snippet"]["title"],
                        "url": "https://www.youtube.com/watch?v=" + i["id"]["videoId"],
                        "text": i["snippet"].get("description", "")
                        + "\n動画の音声・字幕は未取得です。",
                    }
                    for i in data.get("items", [])
                ]
            else:
                data = remote(
                    "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
                    token=token,
                )
                items = [
                    {
                        "title": i["snippet"]["title"],
                        "url": "https://www.youtube.com/channel/" + i["id"],
                        "text": i["snippet"].get("description", ""),
                    }
                    for i in data.get("items", [])
                ]
        elif service == "notion":
            data = remote(
                "https://api.notion.com/v1/search",
                token=token,
                headers={"Notion-Version": "2022-06-28"},
                body={"query": query, "page_size": 10},
            )
            for i in data.get("results", []):
                title = "".join(t.get("plain_text", "") for t in i.get("title", []))
                for p in i.get("properties", {}).values():
                    if p.get("type") == "title":
                        title = "".join(t.get("plain_text", "") for t in p.get("title", []))
                items.append(
                    {
                        "title": title or "Notionページ",
                        "url": i.get("url", ""),
                        "text": "ページ名のみ取得。本文未取得。",
                    }
                )
        elif service == "instagram":
            data = remote(
                "https://graph.instagram.com/v23.0/me/media?fields=id,caption,media_type,permalink,timestamp&limit=10",
                token=token,
            )
            items = [
                {
                    "title": i.get("caption", "投稿")[:100],
                    "url": i.get("permalink", ""),
                    "text": i.get("caption", "") + "\n動画の音声は未取得です。",
                }
                for i in data.get("data", [])
            ]
        elif service == "tiktok":
            data = remote(
                "https://open.tiktokapis.com/v2/video/list/?fields=id,title,video_description,share_url",
                token=token,
                body={"max_count": 10},
            )
            if data.get("error", {}).get("code") not in {None, "ok"}:
                raise ValueError("TikTokの権限・アクセストークンを確認してください。")
            items = [
                {
                    "title": i.get("title", "動画"),
                    "url": i.get("share_url", ""),
                    "text": i.get("video_description", "") + "\n動画の音声は未取得です。",
                }
                for i in data.get("data", {}).get("videos", [])
            ]
        else:
            raise ValueError("このサービスは『外部AIに聞く』から利用してください。")
        return {"items": items, "source": SERVICES[service]}

    def video(self, body):
        if body.get("confirmed") is not True:
            raise ValueError("動画URLの外部送信を確認してください。")
        address = str(body.get("url", ""))
        parsed = url.urlsplit(address)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError("動画のHTTPS URLを指定してください。")
        if parsed.hostname in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}:
            identifier = (
                parsed.path.strip("/")
                if parsed.hostname == "youtu.be"
                else (
                    parsed.path.split("/")[-1]
                    if parsed.path.startswith(("/shorts/", "/embed/"))
                    else url.parse_qs(parsed.query).get("v", [""])[0]
                )
            )
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
                raise ValueError("YouTubeの動画URLを確認してください。")
            address = "https://www.youtube.com/watch?v=" + identifier
            endpoint = "https://www.youtube.com/oembed?" + url.urlencode(
                {"url": address, "format": "json"}
            )
        elif parsed.hostname in {"www.tiktok.com", "tiktok.com"} and re.fullmatch(
            r"/@[\w.]+/video/\d+/?", parsed.path
        ):
            address = "https://www.tiktok.com" + parsed.path
            endpoint = "https://www.tiktok.com/oembed?" + url.urlencode({"url": address})
        elif parsed.hostname in {"www.instagram.com", "instagram.com"} and re.fullmatch(
            r"/(reel|reels|p)/[A-Za-z0-9_-]+/?", parsed.path
        ):
            return {
                "items": [
                    {
                        "title": "Instagram参照リンク",
                        "url": "https://www.instagram.com" + parsed.path,
                        "text": "リンクのみ。動画内容は取得していません。投稿の説明や字幕を貼り付けるか、ローカル動画/音声を取り込んでください。",
                    }
                ]
            }
        else:
            raise ValueError(
                "YouTube・TikTok・Instagramの動画リンクを指定してください。短縮TikTok URLはブラウザで展開してから貼り付けてください。"
            )
        try:
            data = remote(endpoint)
        except ValueError as exc:
            return {
                "items": [
                    {
                        "title": "動画の参照リンク（情報未取得）",
                        "url": address,
                        "text": str(exc)
                        + "\nリンクのみ。動画内容は取得していません。投稿の説明・字幕を貼り付けるか、ローカル音声/動画を取り込んでください。",
                    }
                ],
                "warning": str(exc),
            }
        return {
            "items": [
                {
                    "title": data.get("title", "動画"),
                    "url": address,
                    "text": "投稿者: "
                    + data.get("author_name", "")
                    + "\n公開メタデータのみ。動画の音声・字幕は未取得です。",
                }
            ]
        }

    def preview(self, body):
        service = body.get("service")
        if service not in {"github", "gmail"}:
            raise ValueError("下書きはGitHub IssueまたはGmailに対応しています。")
        title = str(body.get("title", "")).strip()
        text = str(body.get("text", ""))
        target = str(body.get("target", "")).strip()
        if not title or len(title) > 200 or not text or len(text) > 20000:
            raise ValueError("件名と本文を入力してください。")
        if service == "github" and (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]+", target)
            or target.split("/")[-1] in {".", ".."}
        ):
            raise ValueError("GitHubの対象をowner/repo形式で入力してください。")
        if service == "gmail" and not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", target):
            raise ValueError("宛先メールアドレスを確認してください。")
        if any(c in title + target for c in "\r\n"):
            raise ValueError("件名や宛先に改行は使用できません。")
        identifier = secrets.token_urlsafe(24)
        draft = {
            "service": service,
            "target": target,
            "title": title,
            "text": text,
            "expires": time.time() + 300,
        }
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if v["expires"] > time.time()}
            if len(self.previews) >= 20:
                raise ValueError("未確定の下書きが多すぎます。しばらく待ってください。")
            self.previews[identifier] = draft
        return {
            "id": identifier,
            **draft,
            "operation": (
                "GitHubにIssueを投稿"
                if service == "github"
                else "Gmailに下書きを保存（送信しません）"
            ),
        }

    def commit(self, body):
        if body.get("confirmed") is not True:
            raise ValueError("プレビューした内容の投稿/保存を確認してください。")
        with self.lock:
            draft = self.previews.pop(str(body.get("id", "")), None)
        if not draft or draft["expires"] < time.time():
            raise ValueError(
                "下書きが期限切れか既に使用されています。もう一度プレビューしてください。"
            )
        if draft["service"] == "github":
            token = self.vault.load().get("github", {}).get("token")
            if not token:
                raise ValueError("GitHubを接続してください。")
            result = remote(
                "https://api.github.com/repos/" + draft["target"] + "/issues",
                token=token,
                body={"title": draft["title"], "body": draft["text"]},
            )
            return {"url": result["html_url"], "message": "Issueを投稿しました。"}
        values = self.vault.load().get("google", {})
        if "https://www.googleapis.com/auth/gmail.compose" not in values.get("scope", "").split():
            raise ValueError("Google再接続時に『Gmailの下書き保存』を許可してください。")
        message = EmailMessage()
        message["To"] = draft["target"]
        message["Subject"] = draft["title"]
        message.set_content(draft["text"])
        result = remote(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            token=self._google_token(),
            body={"message": {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}},
        )
        return {
            "id": result["id"],
            "url": "https://mail.google.com/mail/u/0/#drafts",
            "message": "Gmailに下書きを保存しました。メールは送信していません。",
        }

    def ai(self, body):
        if body.get("confirmed") is not True:
            raise ValueError("外部AIへの送信内容と費用が発生する可能性を確認してください。")
        service = body.get("service")
        prompt = str(body.get("prompt", ""))
        if (
            service not in {"openai", "gemini", "anthropic"}
            or not prompt.strip()
            or len(prompt) > 20000
        ):
            raise ValueError("外部AIと質問を確認してください。")
        config = self.vault.load().get(service, {})
        token, model = config.get("token"), config.get("model")
        if not token or not model:
            raise ValueError("APIキーとモデルIDを登録してください。")
        if service == "openai":
            data = remote(
                "https://api.openai.com/v1/responses",
                token=token,
                body={"model": model, "input": prompt, "max_output_tokens": 1536, "store": False},
            )
            text = "\n".join(
                c.get("text", "")
                for i in data.get("output", [])
                for c in i.get("content", [])
                if c.get("type") == "output_text"
            )
        elif service == "gemini":
            data = remote(
                "https://generativelanguage.googleapis.com/v1beta/models/"
                + url.quote(model, safe="")
                + ":generateContent",
                headers={"x-goog-api-key": token},
                body={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 1536},
                },
            )
            text = "\n".join(
                p.get("text", "")
                for c in data.get("candidates", [])
                for p in c.get("content", {}).get("parts", [])
            )
        else:
            data = remote(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": token, "anthropic-version": "2023-06-01"},
                body={
                    "model": model,
                    "max_tokens": 1536,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            text = "\n".join(
                c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"
            )
        if not text:
            raise ValueError(
                "外部AIからテキスト回答が返りませんでした。モデルと利用枠を確認してください。"
            )
        return {
            "items": [{"title": SERVICES[service] + " / " + model, "url": "", "text": text[:20000]}]
        }


def gmail_text(payload):
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        try:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
                "utf-8", errors="replace"
            )
        except ValueError:
            return "本文を復号できませんでした。"
    return (
        "\n".join(gmail_text(part) for part in payload.get("parts", []))
        or "プレーンテキスト本文なし（HTML本文/添付ファイルは未取得）。"
    )
