"""Persisted service lifecycle, operator audit and account administration."""
import hashlib
import json
import os
from pathlib import Path
import time


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


DEFAULT = {"history_s": 15, "recognition_interval_s": 10, "intervals": [5, 10, 30], "logging": "debug", "lease_s": 20,
           "history_mib": 64, "history_blocks": 3000, "sync_default_ms": 1200, "sync_max_ms": 1800,
           "version": 1}
SYNC_FIELDS = ("sync_default_ms", "sync_max_ms")


def sync_ms(value):
    if type(value) is not int or not 0 <= value <= 60000:
        raise Problem(400, "同步阈值应为 0–60 秒")
    return value


class Control:
    def __init__(self, conn):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS service (id INTEGER PRIMARY KEY CHECK(id=1),
                maintenance INTEGER NOT NULL, active TEXT NOT NULL, pending TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS room_policy (room_id TEXT PRIMARY KEY, settings TEXT NOT NULL);
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

    def room_settings(self, conn, room_id):
        row = conn.execute("SELECT settings FROM room_policy WHERE room_id=?", (room_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def configure(self, conn, actor, data, room):
        if data.get("version", self.active["version"]) != self.active["version"]:
            raise Problem(409, "配置已更新，请刷新")
        if data.get("room_id", room.room_id) != room.room_id:
            raise Problem(404, "房间不存在")
        config = self.active.copy()
        for key, low, high in (("history_s", 1, 30), ("lease_s", 5, 300),
                               ("recognition_interval_s", 1, 300),
                               ("history_mib", 2, 1024), ("history_blocks", 1, 30000)):
            value = data.get(key, config[key])
            if type(value) is not int or not low <= value <= high:
                raise Problem(400, f"{key} 超出范围 {low}–{high}")
            config[key] = value
        intervals = data.get("intervals", config["intervals"])
        if (not isinstance(intervals, list) or not 1 <= len(intervals) <= 12 or 5 not in intervals or
                any(type(i) is not int or not 1 <= i <= 300 for i in intervals)):
            raise Problem(400, "同步档位须含 5 秒，范围为 1–300 秒")
        config["intervals"] = sorted(set(intervals))
        for key in SYNC_FIELDS:
            config[key] = sync_ms(data.get(key, config[key]))
        before = self.room_settings(conn, room.room_id)
        overrides = before.copy()
        changes = data.get("room_overrides", {})
        if not isinstance(changes, dict) or any(k not in SYNC_FIELDS for k in changes):
            raise Problem(400, "房间配置无效")
        for key, value in changes.items():
            if value is None:
                overrides.pop(key, None)
            else:
                overrides[key] = sync_ms(value)
        if config == self.active and overrides == before and self.pending == self.active:
            return
        delta = {k: [self.active[k], v] for k, v in config.items() if v != self.active[k]}
        if overrides != before:
            delta["room_overrides"] = [before, overrides]
        config["version"] += 1
        payload = json.dumps(config)
        conn.execute("UPDATE service SET active=?, pending=? WHERE id=1", (payload, payload))
        conn.execute("INSERT OR REPLACE INTO room_policy VALUES(?,?)", (room.room_id, json.dumps(overrides)))
        self.audit(conn, actor, f"configure v{config['version']} · {room.room_id} · " + json.dumps(delta, ensure_ascii=False))
        conn.commit()
        self.active, self.pending = config, config.copy()
        room.apply_limits(config, overrides)

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
        elif action == "configure":
            self.configure(conn, actor, data, room)
            return
        elif not self.maintenance:
            raise Problem(409, "请先进入维护模式")
        elif action == "restart":
            trigger = os.environ.get("AUDIMATE_RESTART_FILE")
            if trigger:
                Path(trigger).touch()
            room.reset()
            self.active = self.pending.copy()
            room.apply_limits(self.active, self.room_settings(conn, room.room_id))
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
        if action == "logging":
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
