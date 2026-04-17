from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def main() -> int:
    root = os.getenv("BETTER_AUTH_DB_DIR") or os.getenv("DEER_FLOW_HOME") or str(Path.cwd() / ".deer-flow")
    db_path = Path(root).expanduser() / "better-auth.sqlite"
    print("db_path:", db_path)
    print("exists:", db_path.exists())
    if not db_path.exists():
        return 2

    conn = sqlite3.connect(db_path.as_posix())
    try:
        tables = conn.execute("select name from sqlite_master where type='table' order by name").fetchall()
        print("tables:", [t[0] for t in tables])
        for name, in tables:
            if name.lower() in {"user", "users", "session", "sessions", "account", "accounts"}:
                row = conn.execute("select sql from sqlite_master where type='table' and name = ?", (name,)).fetchone()
                if row and row[0]:
                    print("\n---", name, "---\n", row[0])
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

