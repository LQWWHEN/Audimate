"""Single-worker WSGI app. Accounts and hashed sessions live in SQLite."""
from collections import deque
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit

WEB = Path(__file__).with_name("web")
TTL = 7 * 86400
LEASE = 20


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def body(env, limit):
    try:
        size = int(env.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise Problem(400, "请求长度无效")
    if not 0 <= size <= limit:
        raise Problem(413, "请求过大")
    return env["wsgi.input"].read(size)


def fields(env):
    if env.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
        raise Problem(415, "需要 JSON 数据")
    try:
        data = json.loads(body(env, 8192).decode())
    except (UnicodeError, ValueError):
        raise Problem(400, "数据格式无效")
    if not isinstance(data, dict):
        raise Problem(400, "需要 JSON 对象")
    try:
        for value in data.values():
            if isinstance(value, str):
                value.encode("utf-8")
    except UnicodeError:
        raise Problem(400, "字符编码无效")
    return data


class Room:
    def __init__(self):
        self.lock = threading.Lock()
        self.members = {}
        self.pending = {}
        self.host = None
        self.packets = deque(maxlen=12)
        self.seq = 0

    def leave(self, key):
        self.members.pop(key, None)
        self.pending.pop(key, None)
        if self.host and self.host["key"] == key:
            self.reset()

    def reset(self):
        self.host = None
        self.pending.clear()
        self.packets.clear()
        self.seq = 0

    def touch(self, key, name):
        now = time.monotonic()
        for k, (_, seen) in list(self.members.items()):
            if now - seen > LEASE:
                self.leave(k)
        if key not in self.members and len(self.members) >= 19:
            raise Problem(409, "房间已满")
        self.members[key] = name, now

    def grant(self, key, choice):
        self.reset()
        self.host = dict(choice, key=key, stream=secrets.token_hex(12))

    def status(self, key):
        host = self.host
        mine = bool(host and host["key"] == key)
        return {"host": {k: v for k, v in host.items() if k != "key"} if host else None,
                "hosting": mine, "pending": key in self.pending,
                "requests": [{"id": req["id"], "name": req["name"]} for req in self.pending.values()] if mine else [],
                "members": len(self.members)}

    def action(self, key, name, action, data):
        if action == "request":
            rate, bits = data.get("rate"), data.get("bits")
            label = data.get("source")
            if (type(rate) is not int or not 8000 <= rate <= 192000 or rate % 100 or
                    type(bits) is not int or bits not in (16, 24, 32) or
                    not isinstance(label, str) or not 1 <= len(label.strip()) <= 120):
                raise Problem(400, "音源或格式无效")
            choice = dict(name=name, rate=rate, bits=bits, source=label.strip(), id=secrets.token_hex(8))
            if not self.host:
                self.grant(key, choice)
            elif self.host["key"] != key and key not in self.pending:
                self.pending[key] = choice
        elif action == "leave":
            # A delayed stop from a former stream must not stop a new one.
            if self.host and self.host["key"] == key and self.host["stream"] == data.get("stream"):
                self.reset()
            self.pending.pop(key, None)
        elif action in ("accept", "reject"):
            if not self.host or self.host["key"] != key or self.host["stream"] != data.get("stream"):
                raise Problem(403, "只有现任主持可处理申请")
            applicant = next((k for k, req in self.pending.items() if req["id"] == data.get("id")), None)
            if applicant is None:
                raise Problem(409, "申请已失效")
            request = self.pending.pop(applicant)
            if action == "accept":
                self.grant(applicant, request)
        else:
            raise Problem(404, "操作不存在")


class App:
    def __init__(self, db):
        self.db = str(db)
        self.room = Room()
        self.attempts = {}
        self.throttle = threading.Lock()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users
                    (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, salt BLOB NOT NULL, hash BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions
                    (key TEXT PRIMARY KEY, uid INTEGER NOT NULL REFERENCES users(id), expires INTEGER NOT NULL);
            """)

    def auth(self, conn, action, data, addr):
        name, password = data.get("name"), data.get("password")
        if not isinstance(name, str) or not re.fullmatch(r"[\w-]{2,32}", name) or not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise Problem(400, "用户名需 2–32 个字母、数字、汉字、下划线或短横线；密码需 8–128 个字符")
        with self.throttle:
            now = time.monotonic()
            self.attempts = {ip: v for ip, v in self.attempts.items() if now - v[0] < 60}
            start, hits = self.attempts.get(addr, (now, 0))
            if hits >= 30:
                raise Problem(429, "尝试过于频繁，请稍后重试")
            self.attempts[addr] = (start, hits + 1)
        row = conn.execute("SELECT id,name,salt,hash FROM users WHERE name=?", (name,)).fetchone()
        salt = row[2] if row and action == "login" else os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
        if action == "register":
            try:
                uid = conn.execute("INSERT INTO users(name,salt,hash) VALUES(?,?,?)", (name, salt, digest)).lastrowid
            except sqlite3.IntegrityError:
                raise Problem(409, "用户名已被使用")
        else:
            if not row or not hmac.compare_digest(digest, row[3]):
                raise Problem(401, "用户名或密码错误")
            uid, name = row[:2]
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), uid, int(time.time()) + TTL))
        return name, token

    def dispatch(self, env):
        path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
        assets = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/audio-worker.js": "audio-worker.js"}
        if path in assets and method == "GET":
            file = WEB / assets[path]
            media_type = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}[file.suffix]
            return 200, file.read_bytes(), [("Content-Type", media_type + "; charset=utf-8")]
        if path == "/api/health" and method == "GET":
            return 200, encoded({"service": "audimate", "ok": True}), []
        if method == "POST" and env.get("HTTP_ORIGIN"):
            if urlsplit(env["HTTP_ORIGIN"]).netloc != env.get("HTTP_HOST"):
                raise Problem(403, "来源无效")
        secure = "; Secure" if env.get("wsgi.url_scheme") == "https" else ""
        cookie = "; Path=/; HttpOnly; SameSite=Strict" + secure
        with closing(sqlite3.connect(self.db, timeout=5)) as conn, conn:
            if path in ("/api/login", "/api/register") and method == "POST":
                address = env.get("REMOTE_ADDR", "")
                if os.environ.get("AUDIMATE_TRUST_PROXY") == "1":
                    address = env.get("HTTP_X_REAL_IP", address)
                name, token = self.auth(conn, path[5:], fields(env), address)
                return 200, encoded({"name": name}), [("Set-Cookie", "audimate=" + token + cookie + "; Max-Age=" + str(TTL))]
            match = re.search(r"(?:^|;\s*)audimate=([A-Za-z0-9_-]{43})(?:;|$)", env.get("HTTP_COOKIE", ""))
            key = hashlib.sha256(match[1].encode()).hexdigest() if match else ""
            user = conn.execute("SELECT name FROM users JOIN sessions ON users.id=sessions.uid WHERE key=? AND expires>?",
                                (key, int(time.time()))).fetchone()
            if not user:
                raise Problem(401, "请先登录")
            if path == "/api/me" and method == "GET":
                return 200, encoded({"name": user[0]}), []
            if path == "/api/logout" and method == "POST":
                fields(env)
                conn.execute("DELETE FROM sessions WHERE key=?", (key,))
                with self.room.lock:
                    self.room.leave(key)
                return 200, b"{}", [("Set-Cookie", "audimate=" + cookie + "; Max-Age=0")]
        query = parse_qs(env.get("QUERY_STRING", ""))
        packet, data = None, None
        if method == "POST" and path == "/api/audio":
            if env.get("CONTENT_TYPE") != "application/octet-stream":
                raise Problem(415, "需要 PCM 数据")
            packet = body(env, 768000)
        elif method == "POST":
            data = fields(env)
        room = self.room
        with room.lock:
            room.touch(key, user[0])
            if path.startswith("/api/stage/") and data is not None:
                room.action(key, user[0], path.split("/")[-1], data)
            elif path == "/api/audio" and method in ("GET", "POST"):
                host = room.host
                if not host or query.get("stream", [""])[0] != host["stream"]:
                    raise Problem(409, "主持已变更")
                if packet is not None:
                    if host["key"] != key:
                        raise Problem(403, "仅主持可发送音频")
                    align = 2 * (host["bits"] // 8)
                    if not packet or len(packet) % align or len(packet) > align * host["rate"] // 2:
                        raise Problem(400, "音频块长度无效")
                    room.seq += 1
                    room.packets.append((room.seq, packet))
                    return 200, b"{}", []
                try:
                    after = int(query.get("after", ["-1"])[0])
                except ValueError:
                    raise Problem(400, "序号无效")
                packets = room.packets
                item = packets[-1] if packets and after < 0 else next((p for p in packets if p[0] > after), None)
                if not item:
                    return 204, b"", []
                return 200, item[1], [("Content-Type", "application/octet-stream"), ("X-Sequence", str(item[0]))]
            elif path != "/api/room" or method != "GET":
                raise Problem(404, "接口不存在")
            return 200, encoded(room.status(key)), []

    def __call__(self, env, start_response):
        from http.client import responses
        try:
            code, payload, headers = self.dispatch(env)
        except Problem as exc:
            code, payload, headers = exc.code, encoded({"error": exc.message}), []
        if not any(k == "Content-Type" for k, _ in headers):
            headers.append(("Content-Type", "application/json; charset=utf-8"))
        headers.extend([("Content-Length", str(len(payload))), ("Cache-Control", "no-store"),
                        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
        start_response("{} {}".format(code, responses[code]), headers)
        return [payload]


def create_app():
    return App(os.environ.get("AUDIMATE_DB", "audimate.sqlite3"))
