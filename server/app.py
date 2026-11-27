"""Single-worker WSGI app. Accounts and hashed sessions live in SQLite."""
import math
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
    from . import flac, recognition
    from .monitor import Member, Monitor, client_type
    from .control import DEFAULT, FUZZY_FIELDS, Control, Problem, sync_ms
    from .media import Media, History
else:  # Gunicorn imports app:create_app() from the deployment directory.
    import flac
    import recognition
    from monitor import Member, Monitor, client_type
    from control import DEFAULT, FUZZY_FIELDS, Control, Problem, sync_ms
    from media import Media, History

WEB = Path(__file__).with_name("web")
TTL = 7 * 86400
CAPACITY = 19
OPS_TTL = 8 * 3600


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)


def session_key(token, ops):
    digest = hashlib.sha256(token.encode()).hexdigest()
    return "ops:" + digest if ops else digest


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
    def __init__(self, development=False, room_id="main", *, audio_block_ms=200):
        self.room_id = room_id
        self.policy = DEFAULT.copy()
        self.overrides = {}
        if type(audio_block_ms) is not int or not 10 <= audio_block_ms <= 500 or audio_block_ms % 10:
            raise ValueError("音频块时长须为 10–500 毫秒内的 10 的倍数")
        self.audio_block_ms = audio_block_ms
        self.lock = threading.Lock()
        self.members = {}
        self.pending = {}
        self.host = None
        self.media = Media()
        self.history = History(DEFAULT["history_s"])
        self.recognition = None
        self.recognition_busy = False
        self.lease_s = DEFAULT["lease_s"]
        self.monitor = Monitor(development)
        self.flush_service = None
        self.log_epoch = 0
        self.stage_since = self.last_audio = None
        self.watching = False
        self.lease_grace_until = 0

    def apply_limits(self, config, overrides):
        if self.recognition and self.recognition.title and any(config[k] != self.policy[k] for k in FUZZY_FIELDS):
            self.recognition.candidate = None
        self.policy = config.copy()
        self.overrides = overrides.copy()
        self.history.seconds = config["history_s"]
        self.history.block_limit = config["history_blocks"]
        self.history.byte_limit = config["history_mib"] * 1024**2
        now = time.monotonic()
        if config["lease_s"] < self.lease_s and self.members:
            self.lease_grace_until = now + config["lease_s"]
        self.lease_s = config["lease_s"]
        if self.host:
            self.history.prune(now, 48000 if self.host["codec"] == "opus" else self.host["rate"])
            self.update_recognition()

    def can_recognize(self):
        return recognition.AVAILABLE and self.history.seconds >= recognition.WINDOW_S

    def update_recognition(self):
        if self.host and self.host.get("recognize") and self.can_recognize():
            title = self.host["track"]
            if self.recognition is None or self.recognition.title != title:
                self.recognition = recognition.Recognition(self.history.end_frame, title)
        else:
            self.recognition = None

    def append_audio(self, pcm, rate, width, captured=None, trace=None, *, wire=None):
        self.history.append(pcm, rate, width, captured, trace, wire=wire)
        if self.recognition:
            self.recognition.schedule(self, rate, width)

    def sync_policy(self, *, reference_ms=None):
        # A future room-level latency estimator may supply a reference once it has enough samples.
        cap = min(self.policy["sync_max_ms"], self.overrides.get("sync_max_ms", self.policy["sync_max_ms"]))
        default = self.overrides.get("sync_default_ms", self.policy["sync_default_ms"])
        request = self.host.get("sync_request_ms") if self.host else None
        return {"max_ms": cap, "effective_ms": min(default if request is None else request, cap),
                "suggested_ms": max(1000, round(reference_ms)) if reference_ms is not None else 1000}

    def watch(self):
        while True:
            time.sleep(2)
            with self.lock:
                self.expire(time.monotonic())
                pending = list(self.monitor.service_pending)
                self.monitor.service_pending.clear()
                epoch = self.log_epoch
                if self.host:
                    self.history.prune(time.monotonic(), 48000 if self.host["codec"]=="opus" else self.host["rate"])
                elif not self.members and not pending:
                    self.watching = False
                    return
            if pending and self.flush_service:
                self.flush_service(pending, epoch)

    def start_watcher(self):
        if not self.watching:
            self.watching = True
            threading.Thread(target=self.watch, daemon=True).start()

    def leave(self, key, reason="退出"):
        member = self.members.pop(key, None)
        if member:
            self.monitor.event("连接", f"{member.name} · {reason}")
        self.pending.pop(key, None)
        if self.host and self.host["key"] != key and member:
            self.media.remove(self.host["stream"], key)
        if self.host and self.host["key"] == key:
            self.monitor.event("主持", f"{self.host['name']} 结束主持")
            self.reset()

    def reset(self):
        if self.host:
            member = self.members.get(self.host["key"])
            if member and self.monitor.development:
                member.ended_stream, member.ended_at = self.host["stream"], time.monotonic()
                self.monitor.service_last.clear()
            self.media.stop()
            self.media.remove(self.host["stream"])
        self.host = None
        self.pending.clear()
        self.recognition = None
        self.history.clear()
        self.stage_since = self.last_audio = None
        for member in self.members.values():
            member.last_pull = None

    def expire(self, now):
        if now < self.lease_grace_until:
            return
        for key, member in list(self.members.items()):
            if now - member.seen > self.lease_s:
                self.leave(key, "连接超时")

    def touch(self, key, name, agent):
        now = time.monotonic()
        self.expire(now)
        if key not in self.members:
            if len(self.members) >= CAPACITY:
                raise Problem(409, "房间已满")
            self.members[key] = Member(name, client_type(agent), now, now)
            self.monitor.event("连接", f"{name} 进入房间")
            self.start_watcher()
        self.members[key].seen = now

    def grant(self, key, choice):
        self.reset()
        self.host = dict(choice, key=key, stream=secrets.token_hex(12), track=None, audio_block_ms=self.audio_block_ms)
        self.update_recognition()
        if self.monitor.development:
            self.monitor.round_id = secrets.token_hex(12)
        self.stage_since = time.monotonic()
        self.start_watcher()
        if choice["codec"] == "opus":
            self.media.start_bridge(self.host, self)
        self.monitor.event("主持", f"{choice['name']} 上台 · {choice['rate']} Hz / {choice['bits']}-bit")

    def status(self, key):
        host = self.host
        mine = bool(host and host["key"] == key)
        public_host = {k: v for k, v in host.items() if k != "key"} if host else None
        if host:
            info = self.recognition.view() if self.recognition else None
            public_host["recognition"] = info
            display = host["track"] or (info and info["current"])
            if display:
                public_host["source"] = f"{display['title']} - {display['artist']}"
            elif info:
                public_host["source"] = "尝试匹配中"
        return {"room_id": self.room_id, "sync_policy": self.sync_policy(), "host": public_host,
                "recognition_available": self.can_recognize(),
                "hosting": mine, "pending": key in self.pending,
                "requests": [{"id": req["id"], "name": req["name"]} for req in self.pending.values()] if mine else [],
                "members": len(self.members), "livekit": self.media.enabled, "flac": flac.AVAILABLE}

    def action(self, key, name, action, data):
        if action == "request":
            rate, bits = data.get("rate"), data.get("bits")
            label = data.get("source")
            if (type(rate) is not int or not 8000 <= rate <= 192000 or rate % 100 or
                    type(bits) is not int or bits not in (16, 24, 32) or
                    not isinstance(label, str) or not 1 <= len(label.strip()) <= 120):
                raise Problem(400, "音源或格式无效")
            codec = data.get("codec", "pcm")
            compression = data.get("compression", "none")
            if compression not in ("none", "flac") or compression == "flac" and codec != "pcm":
                raise Problem(400, "只有 PCM 上传支持 FLAC 压缩")
            if compression == "flac" and not flac.AVAILABLE:
                raise Problem(400, "服务端未启用 FLAC")
            if codec not in ("pcm", "opus") or codec == "opus" and not self.media.enabled:
                raise Problem(400, "当前服务不支持所选传输格式")
            bitrate = data.get("bitrate", 128000)
            if codec == "opus" and (type(bitrate) is not int or bitrate not in (64000, 128000, 256000)):
                raise Problem(400, "不支持所选 Opus 码率")
            choice = dict(name=name, rate=rate, bits=bits, source=label.strip(), codec=codec, compression=compression,
                          channels=2, bitrate=bitrate if codec == "opus" else rate * bits * 2, id=secrets.token_hex(8))
            value = data.get("sync_request_ms")
            choice["recognize"] = data.get("recognize", False)
            if type(choice["recognize"]) is not bool or choice["recognize"] and not self.can_recognize():
                raise Problem(400, "当前房间无法启用歌曲识别")
            choice["sync_request_ms"] = None if value is None else sync_ms(value)
            if not self.host:
                self.grant(key, choice)
            elif self.host["key"] != key and key not in self.pending:
                self.pending[key] = choice
                self.monitor.event("主持", f"{name} 申请主持")
        elif action == "sync":
            if not self.host or self.host["stream"] != data.get("stream"):
                raise Problem(409, "主持已变更")
            if self.host["key"] != key:
                raise Problem(403, "仅主持可调整")
            if "sync_request_ms" not in data:
                raise Problem(400, "缺少阈值")
            value = data["sync_request_ms"]
            self.host["sync_request_ms"] = None if value is None else sync_ms(value)
        elif action == "track":
            if not self.host or self.host["stream"] != data.get("stream"):
                raise Problem(409, "主持已变更")
            if self.host["key"] != key:
                raise Problem(403, "仅主持可更新曲目")
            track = data.get("track", {})
            if track is not None:
                if not isinstance(track, dict) or set(track) != {"title", "artist"} or any(
                        not isinstance(s, str) or not s.strip() or len(s) > 160 or
                        not s.isprintable() for s in track.values()):
                    raise Problem(400, "曲目需要有效的歌名和歌手")
                track = {k: v.strip() for k, v in track.items()}
            self.host["track"] = track
            self.update_recognition()
        elif action == "leave":
            # A delayed stop from a former stream must not stop a new one.
            if self.host and self.host["key"] == key and self.host["stream"] == data.get("stream"):
                reasons = {"upload": "上传异常下台", "capture": "采集异常下台"}
                reason = reasons.get(data.get("reason"), "主动下台") if self.monitor.development and isinstance(data.get("reason"), str) else "主动下台"
                self.monitor.event("主持", f"{name} {reason}")
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
    def __init__(self, db, *, development=False):
        self.db = str(db)
        self.room = Room(development)
        self.development = development
        self.log_table = "dev_transport_log" if development else "transport_log"
        if development:
            self.room.flush_service = self.flush_samples
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
            self.control = Control(conn)
            self.room.apply_limits(self.control.active, self.control.room_settings(conn, self.room.room_id))
            self.room.monitor.set_level(self.control.active["logging"])
            if development:
                conn.execute("CREATE TABLE IF NOT EXISTS dev_transport_log (id INTEGER PRIMARY KEY, stamp INTEGER NOT NULL, sample TEXT NOT NULL)")
            conn.execute(f"DELETE FROM {self.log_table} WHERE stamp < ?", (int(time.time())-600,))
            rows = conn.execute(f"SELECT sample FROM {self.log_table} ORDER BY id DESC LIMIT 600")
            self.room.monitor.samples.extend(json.loads(row[0]) for row in rows)
            conn.commit()
        meter = self.room.media.traffic
        if meter.url:
            threading.Thread(target=meter.collect, name="sfu-traffic", daemon=True).start()

    def flush_samples(self, rows, epoch=None):
        try:
            with closing(sqlite3.connect(self.db, timeout=.1)) as conn, conn:
                conn.execute("BEGIN IMMEDIATE")
                if epoch is not None and epoch != self.room.log_epoch:
                    return
                conn.executemany(f"INSERT INTO {self.log_table}(stamp,sample) VALUES(?,?)",
                                 [(r["time"], json.dumps(r, ensure_ascii=False)) for r in rows])
                conn.execute(f"DELETE FROM {self.log_table} WHERE stamp < ? OR id <= (SELECT max(id)-600 FROM {self.log_table})",
                             (int(time.time())-600,))
        except sqlite3.OperationalError:
            if epoch is not None:
                with self.room.lock:
                    if epoch == self.room.log_epoch:
                        pending = self.room.monitor.service_pending
                        available = pending.maxlen - len(pending)
                        if available:
                            pending.extendleft(reversed(rows[-available:]))

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
        row = conn.execute("SELECT id,name,salt,hash,disabled FROM users WHERE name=?", (name,)).fetchone()
        salt = row[2] if row and action == "login" else os.urandom(16)
        digest = password_hash(password, salt)
        # Serialize issuance with maintenance and account changes after the expensive hash.
        conn.execute("BEGIN IMMEDIATE")
        if not ops and conn.execute("SELECT maintenance FROM service WHERE id=1").fetchone()[0]:
            raise Problem(503, "房间维护中，请在重新开放后登录")
        if action != "register":
            row = conn.execute("SELECT id,name,salt,hash,disabled FROM users WHERE name=?", (name,)).fetchone()
        if action == "register":
            try:
                uid = conn.execute("INSERT INTO users(name,salt,hash) VALUES(?,?,?)", (name, salt, digest)).lastrowid
            except sqlite3.IntegrityError:
                raise Problem(409, "用户名已被使用")
        else:
            if not row or not hmac.compare_digest(digest, row[3]) or row[4]:
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
        trace = self.development and self.room.monitor.level != "off" and path == "/api/audio"
        if trace:
            entered = time.monotonic()
        assets = {"/": "index.html", "/app.js": "app.js", "/app.css": "app.css", "/audio-worker.js": "audio-worker.js"}
        assets.update({"/vendor/livekit.js": "vendor/livekit-client-2.25.0.js", "/media.js": "media.js",
                       "/flac-worker.js": "flac-worker.js", "/vendor/flac-1.5.0.wasm": "vendor/flac-1.5.0.wasm",
                       "/ops": "ops/index.html", "/ops/": "ops/index.html",
                       "/ops/style.css": "ops/style.css", "/ops/dashboard.js": "ops/dashboard.js"})
        if self.development:
            assets["/ops/timing-dev.js"] = "ops/timing-dev.js"
            assets["/ops/latency-dev.js"] = "ops/latency-dev.js"
        elif path in ("/ops/timing-dev.js", "/ops/latency-dev.js"):
            raise Problem(404, "资源不存在")
        if path in assets and method == "GET":
            file = WEB / assets[path]
            media_type = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                          ".html": "text/html; charset=utf-8", ".wasm": "application/wasm"}[file.suffix]
            return 200, file.read_bytes(), [("Content-Type", media_type)]
        if path == "/api/health" and method == "GET":
            return 200, encoded({"service": "audimate", "ok": True}), []
        if method == "POST" and env.get("HTTP_ORIGIN"):
            if urlsplit(env["HTTP_ORIGIN"]).netloc != env.get("HTTP_HOST"):
                raise Problem(403, "来源无效")
        if path == "/internal/media-auth":
            # Nginx overrides these headers in an internal-only auth subrequest.
            original = urlsplit(env.get("HTTP_X_ORIGINAL_URI", ""))
            token = parse_qs(original.query).get("access_token", [""])[0] or env.get("HTTP_AUTHORIZATION", "").removeprefix("Bearer ")
            with self.room.lock:
                self.room.expire(time.monotonic())
                if self.control.maintenance or not self.room.media.current_grant(token, self.room):
                    raise Problem(403, "媒体授权已失效")
                return 204, b"", []
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
                conn.commit()
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
                with self.room.lock:
                    conn.execute("DELETE FROM sessions WHERE key=?", (key,))
                    if not ops:
                        self.room.leave(key)
                    conn.commit()
                return 200, b"{}", [("Set-Cookie", cookie_name + "=" + cookie + "; Max-Age=0")]
            if ops:
                if not conn.execute("SELECT 1 FROM operators WHERE uid=?", (user[1],)).fetchone():
                    raise Problem(403, "此账号没有运维权限")
                with self.room.lock:
                    now = time.monotonic()
                    self.room.expire(now)
                    if path == "/ops/api/accounts" and method == "GET":
                        name = parse_qs(env.get("QUERY_STRING", "")).get("name", [""])[0][:32]
                        rows = conn.execute("SELECT name,disabled FROM users WHERE instr(lower(name),lower(?))>0 ORDER BY name LIMIT 100", (name,))
                        return 200, encoded([dict(name=n, disabled=bool(d)) for n,d in rows]), []
                    if path == "/ops/api/accounts" and method == "POST":
                        self.control.account(conn, user[0], fields(env), self.room)
                    elif path.startswith("/ops/api/service/") and method == "POST":
                        self.control.change(conn, user[0], path.rsplit("/", 1)[1], fields(env), self.room)
                    elif path != "/ops/api/overview" or method != "GET":
                        raise Problem(404, "接口不存在")
                    if path == "/ops/api/service/logging" and self.room.monitor.level != "debug":
                        self.room.log_epoch += 1
                        conn.execute(f"DELETE FROM {self.log_table}")
                    conn.execute(f"DELETE FROM {self.log_table} WHERE stamp < ?", (int(time.time())-600,))
                    conn.commit()
                    if self.room.host:
                        history_rate = 48000 if self.room.host["codec"] == "opus" else self.room.host["rate"]
                        self.room.history.prune(now, history_rate)
                    snapshot = self.room.monitor.snapshot(self.room, now, CAPACITY)
                    if self.development:
                        stream = self.room.host["stream"][:8] if self.room.host else next((r["session"] for r in snapshot["samples"]), "")
                        snapshot["timing"] = self.room.monitor.stage_summary(stream)
                        snapshot["timing"]["round_id"] = self.room.monitor.round_id if self.room.host else None
                    snapshot["service"] = self.control.snapshot(conn)
                    snapshot["service"].update(room_id=self.room.room_id, room_overrides=self.room.overrides,
                                               sync_policy=self.room.sync_policy(),
                                               recognition_available=self.room.can_recognize(),
                                               host_request_ms=self.room.host.get("sync_request_ms") if self.room.host else None)
                    snapshot["livekit"] = self.room.media.enabled
                    snapshot["operator"] = user[0]
                    return 200, encoded(snapshot), []
        query = parse_qs(env.get("QUERY_STRING", ""))
        room = self.room
        compression = query.get("compression", ["none"])[0]
        decoded = None
        packet, data = None, None
        if method == "POST" and path == "/api/audio":
            if env.get("CONTENT_TYPE") != "application/octet-stream":
                raise Problem(415, "需要 PCM 数据")
            if trace:
                read_start = time.monotonic()
            packet = body(env, flac.CAPACITY if compression == "flac" else 768000)
            if trace:
                body_ms = (time.monotonic()-read_start)*1000
            if compression == "flac":
                # Validate the bounded stream outside the room lock. The normal
                # commit path below rechecks membership, host and stream after decode.
                with room.lock:
                    host = room.host
                    if not host or [host["stream"]] != query.get("stream"):
                        raise Problem(409, "主持已变更")
                    if key != host["key"]:
                        raise Problem(403, "仅主持可发送音频")
                    if host.get("compression") != "flac":
                        raise Problem(415, "与主持公布的压缩格式不符")
                try:
                    decoded = flac.decode(packet, host["rate"], host["bits"])
                except ValueError as exc:
                    raise Problem(400, str(exc)) from exc
        elif method == "POST":
            data = fields(env)
        room_read = path == "/api/room" and method == "GET"
        stage_write = path in ("/api/stage/request", "/api/stage/leave", "/api/stage/accept",
                               "/api/stage/reject", "/api/stage/track", "/api/stage/sync") and method == "POST"
        audio_request = path == "/api/audio" and method in ("GET", "POST")
        media_request = path in ("/api/media", "/api/diagnostics") and method == "POST"
        clock_read = path == "/api/clock" and method == "GET"
        if not (room_read or stage_write or audio_request or media_request or clock_read):
            raise Problem(404, "接口不存在")
        if trace:
            before_lock = time.monotonic()
        with room.lock:
            trace = trace and room.monitor.level != "off"
            if trace:
                lock_ms = (time.monotonic()-before_lock)*1000
            # The session may have been revoked while the upload body was being read.
            with closing(sqlite3.connect(self.db)) as check:
                if self.control.maintenance or not check.execute("SELECT 1 FROM sessions WHERE key=? AND expires>?", (key, int(time.time()))).fetchone():
                    raise Problem(401, "会话已结束，请重新登录")
            if clock_read:
                return 200, encoded({"unix_ms": time.time() * 1000}), []
            room.touch(key, user[0], env.get("HTTP_USER_AGENT", ""))
            if room_read and room.host and query.get("listen") == [room.host["stream"]]:
                room.members[key].last_pull = time.monotonic()
            if media_request:
                host = room.host
                if path == "/api/diagnostics":
                    sample = room.monitor.record(room.members[key], data, host, is_host=bool(host and key == host["key"]))
                    if sample:
                        if self.development:
                            room.monitor.service_pending.append(sample)
                            room.start_watcher()
                        else:
                            self.flush_samples([sample])
                    return 200, b"{}", []
                if not room.media.enabled:
                    raise Problem(503, "LiveKit 尚未配置")
                if not host or host["stream"] != data.get("stream"):
                    raise Problem(409, "主持已变更")
                publish = data.get("role") == "publish"
                if publish and (host["key"] != key or host["codec"] != "opus"):
                    raise Problem(403, "当前会话没有音轨发布权")
                if publish:
                    offset, error = data.get("clock_offset_ms"), data.get("clock_error_ms")
                    if (type(offset) in (int,float) and abs(offset) <= 86400000 and
                            type(error) in (int,float) and 0 <= error < 5000):
                        host.update(clock_offset_ms=offset, clock_error_ms=error)
                if host["codec"] == "pcm" or not publish:
                    room.media.start_bridge(host, room)
                identity = room.media.identity(host["stream"], key)
                return 200, encoded(dict(url=room.media.url, token=room.media.token(host["stream"], identity,
                                    publish=publish, subscribe=not publish))), []
            if stage_write:
                room.action(key, user[0], path.split("/")[-1], data)
            elif audio_request:
                host = room.host
                if not host or query.get("stream", [""])[0] != host["stream"]:
                    raise Problem(409, "主持已变更")
                if compression != host.get("compression", "none"):
                    raise Problem(415, "请使用支持本轮主持压缩格式的客户端")
                if packet is not None:
                    if host["key"] != key:
                        raise Problem(403, "仅主持可发送音频")
                    if host["codec"] != "pcm":
                        raise Problem(409, "本轮主持使用 LiveKit 音轨")
                    align = 2 * (host["bits"] // 8)
                    pcm = decoded if decoded is not None else packet
                    if not pcm or len(pcm) % align or len(pcm) > align * host["rate"] // 2:
                        raise Problem(400, "音频块长度无效")
                    try:
                        captured = float(query["captured"][0]) if "captured" in query else None
                    except ValueError:
                        raise Problem(400, "采集时刻无效")
                    if captured is not None and (not math.isfinite(captured) or abs(time.time()*1000-captured) > 60000):
                        captured = None
                    detail = None
                    if trace:
                        detail = dict(packet_bytes=len(packet), server_body_ms=body_ms, lock_ms=lock_ms)
                        packet_id = query.get("packet", [""])[0]
                        if re.fullmatch(r"[0-9]{1,12}", packet_id):
                            detail["source_seq"] = int(packet_id)
                        store_start = time.monotonic()
                    room.append_audio(pcm, host["rate"], align // 2, captured, detail,
                                        wire=packet if decoded is not None else None)
                    room.last_audio = time.monotonic()
                    room.monitor.traffic(room.members[key], received=len(packet))
                    if trace:
                        detail.update(server_seq=room.history.sequence, store_ms=(room.last_audio-store_start)*1000,
                                      ingest_ms=(room.last_audio-entered)*1000)
                        if captured is not None:
                            detail["capture_age_ms"] = time.time()*1000-captured
                        sent = query.get("sent", [""])[0]
                        if re.fullmatch(r"[0-9]{12,14}", sent):
                            delay = time.time()*1000-int(sent)
                            if 0 <= delay <= 60000:
                                detail["uplink_est_ms"] = delay
                        room.monitor.service_sample(host["stream"], "ingest", "pcm", detail)
                        if "source_seq" in detail:
                            return 200, encoded({"server_seq":room.history.sequence}), []
                    return 200, b"{}", []
                try:
                    after = int(query.get("after", ["-1"])[0])
                except ValueError:
                    raise Problem(400, "序号无效")
                rate = 48000 if host["codec"] == "opus" else host["rate"]
                item, expired = room.history.read(after, rate, query.get("sync") == ["1"])
                room.members[key].last_pull = time.monotonic()
                if not item:
                    room.monitor.empty_reads += 1
                    return 204, b"", []
                wire = item[7] if compression == "flac" else item[1]
                room.monitor.traffic(room.members[key], sent=len(wire))
                if after >= 0:
                    room.monitor.cursor_gaps += max(0, item[0] - after - 1)
                headers = [("Content-Type", "audio/flac" if compression == "flac" else "application/octet-stream"), ("X-Sequence", str(item[0])),
                           ("X-Frame", str(item[2])), ("X-Behind-Ms", str(round((room.history.end_frame-item[2])*1000/rate))),
                           ("X-Reset", "1" if expired else "0")]
                if item[5] is not None:
                    headers.append(("X-Captured-Ms", str(item[5])))
                if trace and query.get("trace") == ["1"]:
                    timing = dict(item[6] or {}, server_seq=item[0], packet_bytes=len(wire),
                                  residence_ms=(time.monotonic()-item[4])*1000, serve_ms=(time.monotonic()-entered)*1000)
                    headers.append(("X-Audimate-Timing", json.dumps({k:round(v,2) for k,v in timing.items()}, separators=(",",":"))))
                    headers.append(("X-Audimate-Served-Ms", str(round(time.time()*1000))))
                return 200, wire, headers
            config = dict(self.control.active, dev_timing=True) if self.development else self.control.active
            return 200, encoded(dict(room.status(key), config=config)), []

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
                        ("Content-Security-Policy", "default-src 'self'; " +
                         "img-src 'self' https://*.mzstatic.com https://*.shazam.com; " +
                         ("script-src 'self' 'wasm-unsafe-eval'; " if path == "/flac-worker.js" else "") +
                         "connect-src 'self' wss: ws://localhost:* ws://127.0.0.1:*; media-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")])
        start_response("{} {}".format(code, responses[code]), headers)
        return [payload]


def create_app():
    return App(os.environ.get("AUDIMATE_DB", "audimate.sqlite3"))


def create_dev_app():
    return App(os.environ.get("AUDIMATE_DB", "audimate-dev.sqlite3"), development=True)
