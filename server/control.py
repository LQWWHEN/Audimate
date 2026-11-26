"""Persisted service lifecycle, operator audit and account administration."""
import hashlib
import json
import os
from pathlib import Path
import time


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


DEFAULT = {"history_s": 5, "intervals": [5, 10, 30], "logging": "debug", "lease_s": 20}


class Control:
    def __init__(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS service (id INTEGER PRIMARY KEY CHECK(id=1),
                maintenance INTEGER NOT NULL, active TEXT NOT NULL, pending TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, stamp INTEGER, actor TEXT, action TEXT);
            CREATE TABLE IF NOT EXISTS transport_log (id INTEGER PRIMARY KEY, stamp INTEGER NOT NULL, sample TEXT NOT NULL);
        """)
        if "disabled" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}:
            conn.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
        defaults = json.dumps(DEFAULT)
        conn.execute("INSERT OR IGNORE INTO service VALUES(1,0,?,?)", (defaults, defaults))
        maintenance, active, pending = conn.execute("SELECT maintenance,active,pending FROM service").fetchone()
        self.maintenance = bool(maintenance)
        active_config = DEFAULT | json.loads(active)
        self.pending = active_config | json.loads(pending)
        self.active = self.pending.copy() if maintenance else active_config
        conn.execute("UPDATE service SET active=?, pending=? WHERE id=1",
                     (json.dumps(self.active), json.dumps(self.pending)))
        conn.commit()

    def snapshot(self, conn):
        trigger = os.environ.get("AUDIMATE_RESTART_FILE")
        return dict(maintenance=self.maintenance, restarting=bool(trigger and Path(trigger).exists()),
                    active=self.active, pending=self.pending,
                    audit=[dict(time=t, actor=u, action=a) for t,u,a in conn.execute(
                        "SELECT stamp,actor,action FROM audit ORDER BY id DESC LIMIT 30")])

    def audit(self, conn, actor, action):
        conn.execute("INSERT INTO audit(stamp,actor,action) VALUES(?,?,?)", (int(time.time()), actor, action))
        conn.execute("DELETE FROM audit WHERE id <= (SELECT max(id)-500 FROM audit)")

    def change(self, conn, actor, action, data, room):
        if action == "maintenance":
            conn.execute("DELETE FROM sessions WHERE key NOT LIKE 'ops:%'")
            room.reset()
            room.members.clear()
            self.maintenance = True
        elif action == "logging":
            level = data.get("logging")
            if level not in ("off", "errors", "debug"):
                raise Problem(400, "日志级别无效")
            self.active = dict(self.active, logging=level)
            self.pending = dict(self.pending, logging=level)
            room.monitor.set_level(level)
        elif not self.maintenance:
            raise Problem(409, "请先进入维护模式")
        elif action == "configure":
            history, intervals = data.get("history_s"), data.get("intervals")
            if type(history) is not int or not 1 <= history <= 30:
                raise Problem(400, "历史缓存应为 1–30 秒")
            if (not isinstance(intervals, list) or not 1 <= len(intervals) <= 12 or 5 not in intervals or
                    any(type(i) is not int or not 1 <= i <= 300 for i in intervals)):
                raise Problem(400, "同步档位应为 1–300 秒的整数，须包含默认 5 秒")
            lease = data.get("lease_s", self.pending["lease_s"])
            if type(lease) is not int or not 5 <= lease <= 300:
                raise Problem(400, "成员离线超时应为 5–300 的整数秒")
            self.pending = dict(self.pending, history_s=history, intervals=sorted(set(intervals)), lease_s=lease)
        elif action == "restart":
            trigger = os.environ.get("AUDIMATE_RESTART_FILE")
            if trigger:
                Path(trigger).touch()
            room.reset()
            self.active = self.pending.copy()
            room.apply_limits(self.active)
        elif action == "open":
            if self.active != self.pending:
                raise Problem(409, "请先重启服务，使暂存配置生效")
            trigger = os.environ.get("AUDIMATE_RESTART_FILE")
            if trigger and Path(trigger).exists():
                raise Problem(409, "媒体服务正在重启，请稍后开放")
            self.maintenance = False
        else:
            raise Problem(404, "操作不存在")
        conn.execute("UPDATE service SET maintenance=?,active=?,pending=? WHERE id=1",
                     (self.maintenance, json.dumps(self.active), json.dumps(self.pending)))
        detail = action
        if action == "configure":
            detail += f" · 缓存 {self.pending['history_s']}s · 校准 {self.pending['intervals']} · 离线超时 {self.pending['lease_s']}s"
        elif action == "logging":
            detail += " · " + self.active["logging"]
        self.audit(conn, actor, detail)

    def account(self, conn, actor, data, room):
        name, action = data.get("name"), data.get("action")
        if not isinstance(name, str):
            raise Problem(400, "请输入账号名")
        row = conn.execute("SELECT id,name FROM users WHERE name=?", (name,)).fetchone()
        if not row:
            raise Problem(404, "账号不存在")
        uid, name = row
        if conn.execute("SELECT 1 FROM operators WHERE uid=?", (uid,)).fetchone():
            raise Problem(403, "运维账号请使用服务器上的账号工具管理")
        if action in ("disable", "enable"):
            conn.execute("UPDATE users SET disabled=? WHERE id=?", (action == "disable", uid))
        elif action == "password":
            password = data.get("password")
            if not isinstance(password, str) or not 8 <= len(password) <= 128:
                raise Problem(400, "新密码需 8–128 个字符")
            salt = os.urandom(16)
            digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
            conn.execute("UPDATE users SET hash=?,salt=? WHERE id=?", (digest, salt, uid))
        elif action != "revoke":
            raise Problem(400, "未知账号操作")
        if action != "enable":
            for key, in conn.execute("SELECT key FROM sessions WHERE uid=?", (uid,)):
                room.leave(key, "运维结束会话")
            conn.execute("DELETE FROM sessions WHERE uid=?", (uid,))
        self.audit(conn, actor, action + " · " + name)
