"""Local management of read-only operators for an initialized Audimate database."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import re
import secrets
import sqlite3

if __package__:
    from .app import password_hash
else:
    from app import password_hash


def manage(db, action, name):
    if not re.fullmatch(r"[\w-]{2,32}", name):
        raise ValueError("账号名需要 2–32 个字母、数字、汉字、下划线或短横线")
    # mode=rw makes a mistyped path an error instead of creating an empty DB.
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=rw", uri=True)) as conn, conn:
        row = conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
        if action == "create":
            if row:
                raise ValueError("账号名已占用，请指定新账号或明确授权已有账号")
            password, salt = secrets.token_urlsafe(24), os.urandom(16)
            uid = conn.execute("INSERT INTO users(name,salt,hash) VALUES(?,?,?)",
                               (name, salt, password_hash(password, salt))).lastrowid
            conn.execute("INSERT INTO operators(uid) VALUES(?)", (uid,))
            return password
        if not row:
            raise ValueError("找不到账号")
        if action == "grant":
            conn.execute("INSERT OR IGNORE INTO operators(uid) VALUES(?)", row)
        elif action == "revoke":
            conn.execute("DELETE FROM operators WHERE uid=?", row)
            conn.execute("DELETE FROM sessions WHERE uid=? AND key LIKE 'ops:%'", row)
        else:
            raise ValueError("操作无效")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("action", choices=("create", "grant", "revoke"))
    parser.add_argument("name")
    args = parser.parse_args()
    try:
        secret = manage(args.db, args.action, args.name)
    except (ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    if secret:
        print(f"Username: {args.name}\nPassword: {secret}")
