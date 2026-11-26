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
import traceback
from urllib.parse import parse_qs, urlsplit

if __package__:
    from .monitor import Member, Monitor, client_type
else:  # Gunicorn imports app:create_app() from the deployment directory.
    from monitor import Member, Monitor, client_type

WEB = Path(__file__).with_name("web")
TTL = 7 * 86400
LEASE = 20
CAPACITY = 19
OPS_TTL = 8 * 3600


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)


def session_key(token, ops):
    digest = hashlib.sha256(token.encode()).hexdigest()
    return "ops:" + digest if ops else digest


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
        self.monitor = Monitor()
        self.stage_since = self.last_audio = None

    def leave(self, key, reason="退出"):
        member = self.members.pop(key, None)
        if member:
            self.monitor.event("连接", f"{member.name} · {reason}")
        self.pending.pop(key, None)
        if self.host and self.host["key"] == key:
            self.monitor.event("主持", f"{self.host['name']} 结束主持")
            self.reset()

    def reset(self):
        self.host = None
        self.pending.clear()
        self.packets.clear()
        self.seq = 0
        self.stage_since = self.last_audio = None
        for member in self.members.values():
            member.last_pull = None

    def expire(self, now):
        for key, member in list(self.members.items()):
            if now - member.seen > LEASE:
                self.leave(key, "连接超时")

    def touch(self, key, name, agent):
        now = time.monotonic()
        self.expire(now)
        if key not in self.members:
            if len(self.members) >= CAPACITY:
                raise Problem(409, "房间已满")
            self.members[key] = Member(name, client_type(agent), now, now)
            self.monitor.event("连接", f"{name} 进入房间")
        self.members[key].seen = now

    def grant(self, key, choice):
        self.reset()
        self.host = dict(choice, key=key, stream=secrets.token_hex(12))
        self.stage_since = time.monotonic()
        self.monitor.event("主持", f"{choice['name']} 上台 · {choice['rate']} Hz / {choice['bits']}-bit")

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
                self.monitor.event("主持", f"{name} 申请主持")
        elif action == "leave":
            # A delayed stop from a former stream must not stop a new one.
            if self.host and self.host["key"] == key and self.host["stream"] == data.get("stream"):
                self.monitor.event("主持", f"{name} 主动下台")
                self.reset()
            if key in self.pending:
                self.monitor.event("主持", f"{name} 取消申请")
            self.pending.pop(key, None)
        elif action in ("accept", "reject"):
            if not self.host or self.host["key"] != key or self.host["stream"] != data.get("stream"):
                raise Problem(403, "只有现任主持可处理申请")
            applicant = next((k for k, req in self.pending.items() if req["id"] == data.get("id")), None)
            if applicant is None:
                raise Problem(409, "申请已失效")
            request = self.pending.pop(applicant)
            decision = "接受" if action == "accept" else "拒绝"
            self.monitor.event("主持", f"{name} {decision}了 {request['name']} 的申请")
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
                CREATE TABLE IF NOT EXISTS operators
                    (uid INTEGER PRIMARY KEY REFERENCES users(id));
            """)

    def auth(self, conn, action, data, addr, ops=False):
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
        digest = password_hash(password, salt)
        if action == "register":
            try:
                uid = conn.execute("INSERT INTO users(name,salt,hash) VALUES(?,?,?)", (name, salt, digest)).lastrowid
            except sqlite3.IntegrityError:
                raise Problem(409, "用户名已被使用")
        else:
            if not row or not hmac.compare_digest(digest, row[3]):
                raise Problem(401, "用户名或密码错误")
            uid, name = row[:2]
            if ops and not conn.execute("SELECT 1 FROM operators WHERE uid=?", (uid,)).fetchone():
                raise Problem(403, "此账号没有运维权限")
        token = secrets.token_urlsafe(32)
        conn.execute("DELETE FROM sessions WHERE expires<=?", (int(time.time()),))
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (session_key(token, ops), uid, int(time.time()) + (OPS_TTL if ops else TTL)))
        return name, token

    def dispatch(self, env):
        path, method = env["PATH_INFO"], env["REQUEST_METHOD"]
        assets = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/audio-worker.js": "audio-worker.js"}
        assets.update({"/ops": "ops/index.html", "/ops/": "ops/index.html",
                       "/ops/style.css": "ops/style.css", "/ops/dashboard.js": "ops/dashboard.js"})
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
        ops = path.startswith("/ops/")
        cookie_name = "audimate_ops" if ops else "audimate"
        cookie = ("; Path=/ops/" if ops else "; Path=/") + "; HttpOnly; SameSite=Strict" + secure
        with closing(sqlite3.connect(self.db, timeout=5)) as conn, conn:
            if path in ("/api/login", "/api/register", "/ops/api/login") and method == "POST":
                address = env.get("REMOTE_ADDR", "")
                if os.environ.get("AUDIMATE_TRUST_PROXY") == "1":
                    address = env.get("HTTP_X_REAL_IP", address)
                name, token = self.auth(conn, path.rsplit("/", 1)[1], fields(env), address, ops)
                value = cookie_name + "=" + token + cookie + "; Max-Age=" + str(OPS_TTL if ops else TTL)
                return 200, encoded({"name": name}), [("Set-Cookie", value)]
            match = re.search(r"(?:^|;\s*)" + cookie_name + r"=([A-Za-z0-9_-]{43})(?:;|$)", env.get("HTTP_COOKIE", ""))
            key = session_key(match[1], ops) if match else ""
            user = conn.execute("SELECT name,uid FROM users JOIN sessions ON users.id=sessions.uid WHERE key=? AND expires>?",
                                (key, int(time.time()))).fetchone()
            if not user:
                raise Problem(401, "请先登录")
            if path == "/api/me" and method == "GET":
                return 200, encoded({"name": user[0]}), []
            if path in ("/api/logout", "/ops/api/logout") and method == "POST":
                fields(env)
                conn.execute("DELETE FROM sessions WHERE key=?", (key,))
                if not ops:
                    with self.room.lock:
                        self.room.leave(key)
                return 200, b"{}", [("Set-Cookie", cookie_name + "=" + cookie + "; Max-Age=0")]
            if ops:
                if path != "/ops/api/overview" or method != "GET":
                    raise Problem(404, "接口不存在")
                if not conn.execute("SELECT 1 FROM operators WHERE uid=?", (user[1],)).fetchone():
                    raise Problem(403, "此账号没有运维权限")
                with self.room.lock:
                    now = time.monotonic()
                    self.room.expire(now)
                    snapshot = self.room.monitor.snapshot(self.room, now, CAPACITY, LEASE)
                    snapshot["operator"] = user[0]
                    return 200, encoded(snapshot), []
        query = parse_qs(env.get("QUERY_STRING", ""))
        packet, data = None, None
        if method == "POST" and path == "/api/audio":
            if env.get("CONTENT_TYPE") != "application/octet-stream":
                raise Problem(415, "需要 PCM 数据")
            packet = body(env, 768000)
        elif method == "POST":
            data = fields(env)
        room = self.room
        room_read = path == "/api/room" and method == "GET"
        stage_write = path in ("/api/stage/request", "/api/stage/leave", "/api/stage/accept", "/api/stage/reject") and method == "POST"
        audio_request = path == "/api/audio" and method in ("GET", "POST")
        if not (room_read or stage_write or audio_request):
            raise Problem(404, "接口不存在")
        with room.lock:
            room.touch(key, user[0], env.get("HTTP_USER_AGENT", ""))
            if stage_write:
                room.action(key, user[0], path.split("/")[-1], data)
            elif audio_request:
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
                    room.last_audio = time.monotonic()
                    room.monitor.traffic(room.members[key], received=len(packet))
                    return 200, b"{}", []
                try:
                    after = int(query.get("after", ["-1"])[0])
                except ValueError:
                    raise Problem(400, "序号无效")
                packets = room.packets
                item = packets[-1] if packets and after < 0 else next((p for p in packets if p[0] > after), None)
                room.members[key].last_pull = time.monotonic()
                if not item:
                    room.monitor.empty_reads += 1
                    return 204, b"", []
                room.monitor.traffic(room.members[key], sent=len(item[1]))
                if after >= 0:
                    room.monitor.cursor_gaps += max(0, item[0] - after - 1)
                return 200, item[1], [("Content-Type", "application/octet-stream"), ("X-Sequence", str(item[0]))]
            return 200, encoded(room.status(key)), []

    def __call__(self, env, start_response):
        from http.client import responses
        error = ""
        try:
            code, payload, headers = self.dispatch(env)
        except Problem as exc:
            error = exc.message
            code, payload, headers = exc.code, encoded({"error": error}), []
        except Exception:
            traceback.print_exc(file=env["wsgi.errors"])
            error = "服务内部错误"
            code, payload, headers = 500, encoded({"error": error}), []
        path = env["PATH_INFO"]
        if code >= 400 and (path in ("/api/room", "/api/audio") or path.startswith("/api/stage/")):
            route = "stage" if path.startswith("/api/stage/") else path[5:]
            method = env["REQUEST_METHOD"] if env["REQUEST_METHOD"] in ("GET", "POST") else "其他方法"
            with self.room.lock:
                self.room.monitor.failures += 1
                self.room.monitor.event("异常", f"{method} {route} · HTTP {code} · {error}")
        if not any(k == "Content-Type" for k, _ in headers):
            headers.append(("Content-Type", "application/json; charset=utf-8"))
        headers.extend([("Content-Length", str(len(payload))), ("Cache-Control", "no-store"),
                        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
                        ("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
        start_response("{} {}".format(code, responses[code]), headers)
        return [payload]


def create_app():
    return App(os.environ.get("AUDIMATE_DB", "audimate.sqlite3"))
