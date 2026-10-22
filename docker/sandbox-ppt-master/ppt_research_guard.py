"""Host-local, task-scoped circuit breaker shared by every research MCP process.

Not an authorization boundary, provider quota, or Skill stage machine. Store only
keyed fingerprints, counters and allowlisted categories; never tool inputs,
results, capabilities or raw exceptions. The host creates this on local storage,
not the shared user-data filesystem. Durable task state retains its safe summary.
"""

import hashlib
import os
import re
import sqlite3
import stat
from contextlib import closing, contextmanager
from pathlib import Path

ERRORS = frozenset(
    {
        "tls_verification",
        "tls_error",
        "proxy_connection",
        "network_timeout",
        "network_connection",
        "provider_auth",
        "provider_quota",
        "provider_rate_limit",
        "provider_limit_unknown",
        "provider_http",
        "invalid_arguments",
        "capability_rejected",
        "run_inactive",
        "research_budget",
        "service_busy",
        "gateway_unavailable",
        "invalid_response",
        "extract_failed",
        "unknown_research_failure",
        "guard_unavailable",
    }
)
MAX_ATTEMPTS = 64
MAX_IDENTICAL_FAILURES = 3
MAX_CONSECUTIVE_FAILURES = 8


class ResearchStopped(RuntimeError):
    pass


class ResearchGuard:
    def __init__(self, path, capability):
        self.path = Path(path)
        self.scope = hashlib.sha256(capability.encode()).hexdigest()

    @classmethod
    def create(cls, path, capability):
        guard = cls(path, capability)
        fd = os.open(guard.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(guard.path, timeout=2)) as db:
            db.executescript("""
                CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL,
                    attempts INTEGER NOT NULL, failures INTEGER NOT NULL,
                    reason TEXT NOT NULL, last_error TEXT NOT NULL);
                CREATE TABLE calls (id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
                    outcome TEXT NOT NULL);
                CREATE TABLE keys (fingerprint TEXT PRIMARY KEY, failures INTEGER NOT NULL);
            """)
            db.execute("INSERT INTO state VALUES (1, ?, 0, 0, '', '')", (guard.scope,))
            db.commit()
        return guard

    @contextmanager
    def _transaction(self):
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or self.path.resolve() != self.path.absolute()
            or (
                os.name == "posix"
                and (info.st_uid != os.getuid() or info.st_mode & 0o077)
            )
            or info.st_size > 1024 * 1024
        ):
            raise ValueError("unsafe_research_guard")
        # mode=rw cannot silently create a new guard when the original disappears.
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=2)
        try:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT scope FROM state WHERE id=1").fetchone()
            if state != (self.scope,):
                raise ValueError("research_guard_scope_mismatch")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def reserve(self, fingerprint):
        if (
            not isinstance(fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
        ):
            raise ValueError("invalid_fingerprint")
        stopped = False
        with self._transaction() as db:
            attempts, reason = db.execute(
                "SELECT attempts, reason FROM state"
            ).fetchone()
            if reason:
                stopped = True
            elif attempts >= MAX_ATTEMPTS:
                db.execute("UPDATE state SET reason='research_attempt_budget'")
                stopped = True
            else:
                ticket = attempts + 1
                db.execute("UPDATE state SET attempts=?", (ticket,))
                db.execute(
                    "INSERT INTO calls VALUES (?, ?, 'pending')", (ticket, fingerprint)
                )
        if stopped:
            raise ResearchStopped("research_guard_blocked")
        return ticket

    def finish(self, ticket, error):
        outcome = (
            "success"
            if error is None
            else error
            if error in ERRORS
            else "unknown_research_failure"
        )
        with self._transaction() as db:
            row = db.execute(
                "SELECT fingerprint, outcome FROM calls WHERE id=?", (ticket,)
            ).fetchone()
            if row is None or row[1] != "pending":
                return  # A duplicated delivery never counts again or unblocks a stop.
            key = row[0]
            db.execute("UPDATE calls SET outcome=? WHERE id=?", (outcome, ticket))
            if outcome == "success":
                db.execute("UPDATE state SET failures=0")
                db.execute("INSERT OR REPLACE INTO keys VALUES (?, 0)", (key,))
                return
            db.execute("UPDATE state SET failures=failures+1, last_error=?", (outcome,))
            db.execute("INSERT OR IGNORE INTO keys VALUES (?, 0)", (key,))
            db.execute(
                "UPDATE keys SET failures=failures+1 WHERE fingerprint=?", (key,)
            )
            failures, reason = db.execute(
                "SELECT failures, reason FROM state"
            ).fetchone()
            same = db.execute(
                "SELECT failures FROM keys WHERE fingerprint=?", (key,)
            ).fetchone()[0]
            if not reason:
                reason = (
                    "repeated_research_failure"
                    if same >= MAX_IDENTICAL_FAILURES
                    else "consecutive_research_failures"
                    if failures >= MAX_CONSECUTIVE_FAILURES
                    else ""
                )
                if reason:
                    db.execute("UPDATE state SET reason=?", (reason,))

    def snapshot(self):
        with self._transaction() as db:
            attempts, failures, reason, error = db.execute(
                "SELECT attempts, failures, reason, last_error FROM state"
            ).fetchone()
        return {
            "attempts": attempts,
            "consecutive_failures": failures,
            "blocked": bool(reason),
            "reason": reason,
            "last_error": error,
        }
