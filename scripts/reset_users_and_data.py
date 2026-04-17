from __future__ import annotations

import argparse
import hashlib
import secrets
import shutil
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _gen_id() -> str:
    return secrets.token_urlsafe(24)


def _hash_password(password: str) -> str:
    salt_hex = secrets.token_bytes(16).hex()
    normalized = unicodedata.normalize("NFKC", password)
    dk = hashlib.scrypt(
        normalized.encode("utf-8"),
        salt=salt_hex.encode("utf-8"),
        n=16384,
        r=16,
        p=1,
        dklen=64,
        maxmem=128 * 16384 * 16 * 2,
    )
    return f"{salt_hex}:{dk.hex()}"


@dataclass(frozen=True)
class Paths:
    repo_root: Path

    @property
    def frontend_db(self) -> Path:
        return self.repo_root / "frontend" / ".deer-flow" / "better-auth.sqlite"

    @property
    def backend_home(self) -> Path:
        return self.repo_root / "backend" / ".deer-flow"


def _ensure_frontend_db_exists(db_path: Path) -> None:
    if db_path.exists():
        return
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path.as_posix())
    try:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "user" ("id" text not null primary key, "name" text not null, "email" text not null unique, "emailVerified" integer not null, "image" text, "createdAt" date not null, "updatedAt" date not null)'
        )
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "account" ("id" text not null primary key, "accountId" text not null, "providerId" text not null, "userId" text not null references "user" ("id") on delete cascade, "accessToken" text, "refreshToken" text, "idToken" text, "accessTokenExpiresAt" date, "refreshTokenExpiresAt" date, "scope" text, "password" text, "createdAt" date not null, "updatedAt" date not null)'
        )
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "session" ("id" text not null primary key, "expiresAt" date not null, "token" text not null unique, "createdAt" date not null, "updatedAt" date not null, "ipAddress" text, "userAgent" text, "userId" text not null references "user" ("id") on delete cascade)'
        )
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "verification" ("id" text not null primary key, "identifier" text not null, "value" text not null, "expiresAt" date not null, "createdAt" date not null, "updatedAt" date not null)'
        )
        conn.commit()
    finally:
        conn.close()


def reset_all(
    paths: Paths,
    *,
    admin_email: str,
    admin_password: str,
    admin_name: str,
    allow_signup: bool,
) -> None:
    _ensure_frontend_db_exists(paths.frontend_db)
    conn = sqlite3.connect(paths.frontend_db.as_posix())
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            DELETE FROM session;
            DELETE FROM account;
            DELETE FROM verification;
            DELETE FROM user;
            DELETE FROM platform_user_flags;
            DELETE FROM platform_settings;
            """
        )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS platform_user_flags (
              user_id TEXT PRIMARY KEY REFERENCES user(id) ON DELETE CASCADE,
              is_admin INTEGER NOT NULL DEFAULT 0,
              is_disabled INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS platform_settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )

        now = _iso_now()
        user_id = _gen_id()
        account_id = _gen_id()
        account_row_id = _gen_id()
        conn.execute(
            'INSERT INTO "user"(id, name, email, emailVerified, image, createdAt, updatedAt) VALUES(?, ?, ?, ?, ?, ?, ?)',
            (user_id, admin_name, admin_email, 0, None, now, now),
        )
        conn.execute(
            'INSERT INTO "account"(id, accountId, providerId, userId, password, createdAt, updatedAt) VALUES(?, ?, ?, ?, ?, ?, ?)',
            (account_row_id, account_id, "credential", user_id, _hash_password(admin_password), now, now),
        )
        conn.execute(
            "INSERT INTO platform_user_flags(user_id, is_admin, is_disabled, updated_at) VALUES(?, 1, 0, datetime('now'))",
            (user_id,),
        )
        conn.execute(
            "INSERT INTO platform_settings(key, value, updated_at) VALUES('allow_signup', ?, datetime('now'))",
            ("1" if allow_signup else "0",),
        )
        conn.commit()
    finally:
        conn.close()

    if paths.backend_home.exists():
        shutil.rmtree(paths.backend_home, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-email", default="admin@example.com")
    parser.add_argument("--admin-password", default="Admin123456!")
    parser.add_argument("--admin-name", default="管理员")
    parser.add_argument("--allow-signup", default="0", choices=["0", "1"])
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    paths = Paths(repo_root=repo_root)
    reset_all(
        paths,
        admin_email=args.admin_email,
        admin_password=args.admin_password,
        admin_name=args.admin_name,
        allow_signup=args.allow_signup == "1",
    )
    print("Reset done.")
    print("Admin email:", args.admin_email)
    print("Admin password:", args.admin_password)
    print("allow_signup:", args.allow_signup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
