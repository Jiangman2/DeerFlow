"""One-shot, source-bound OA expansion through the installed bootstrap only.

Run in the existing consumer's isolated Python process, never import the app.
The controller owns its durable once/uncertain marker before invoking expand.
All monkey patches and the additional migration disappear with this process.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
from urllib.parse import urlsplit


BASE = "0020_ppt_confirmation"
TARGET = "0021_oa_result_sets"
MIGRATION_NAME = "versions/0021_oa_result_sets.py"
MIGRATION_SHA256 = "f0f4e5571251359f12ca023b864542a69fda97f990bf476dbf366d9039657347"
LOCK_TIMEOUT_MS = 5000
STATEMENT_TIMEOUT_MS = 30000
PROCESS_DEADLINE_SECONDS = 120
OLD_COLUMNS = frozenset((
    "id", "idempotency_key_hash", "request_id", "request_fingerprint", "owner_user_id", "assistant_id", "thread_id", "run_id", "status", "callback_status",
    "document_filename", "key_fields", "output_virtual_path", "output_filename", "output_sha256", "process_attempts", "callback_attempts", "next_attempt_at",
    "lease_owner", "lease_expires_at", "last_error_code", "last_error_detail", "last_callback_error", "last_callback_status_code", "prepared_at", "completed_at",
    "delivered_at", "created_at", "updated_at",
))


class Refused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(value, code):
    if not value:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fingerprint(url):
    parsed = urlsplit(url)
    return sha(json.dumps([parsed.hostname, parsed.port or 5432, parsed.path], sort_keys=True).encode())


def read_regular(path, *, limit=262144):
    """Do not dereference links or special files; bound and recheck each read."""
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink() and before.st_size <= limit, "UNSAFE_SOURCE_FILE")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    require(len(raw) <= limit and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "SOURCE_CHANGED_DURING_READ")
    final = path.lstat()
    require((final.st_dev, final.st_ino, final.st_mode, final.st_size, final.st_mtime_ns) ==
            (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns), "SOURCE_CHANGED_DURING_READ")
    return raw


def migration_tree(root, expected):
    """Bind both copied code and the installed helpers imported by env.py."""
    require(isinstance(expected, dict) and 20 <= len(expected) <= 64, "MIGRATION_PIN_REQUIRED")
    require(not root.is_symlink() and root.is_dir(), "UNSAFE_MIGRATION_ROOT")
    observed, total, count = {}, 0, 0
    for directory, directories, files in os.walk(root, followlinks=False):
        require(len(Path(directory).relative_to(root).parts) <= 2, "MIGRATION_TREE_DEPTH")
        for name in directories:
            require(not (Path(directory) / name).is_symlink(), "MIGRATION_TREE_SYMLINK")
        directories[:] = [name for name in directories if name != "__pycache__"]
        for name in files:
            count += 1
            require(count <= 128, "MIGRATION_TREE_LIMIT")
            path = Path(directory) / name
            require(not path.is_symlink(), "MIGRATION_TREE_SYMLINK")
            if path.suffix != ".py":
                continue
            relative = path.relative_to(root).as_posix()
            require(relative in expected, "UNEXPECTED_MIGRATION_SOURCE")
            raw = read_regular(path)
            require(sha(raw) == expected[relative], "MIGRATION_SOURCE_HASH")
            observed[relative] = raw
            total += len(raw)
            require(total <= 2097152, "MIGRATION_TREE_LIMIT")
    require(set(observed) == set(expected), "MIGRATION_SOURCE_MISSING")
    require({"env.py", "_helpers.py", "_env_filters.py"} <= set(observed) and MIGRATION_NAME not in observed, "MIGRATION_TREE_SHAPE")
    digest = sha(json.dumps({name: sha(raw) for name, raw in observed.items()}, sort_keys=True).encode())
    return observed, digest


def error_metadata(exc):
    """No exception messages, SQL parameters, host names or credentials."""
    if isinstance(exc, Refused):
        return {"error_code": exc.code, "error_category": "refused"}
    current, seen, sqlstate = exc, set(), None
    while current is not None and id(current) not in seen and len(seen) < 12:
        seen.add(id(current))
        candidate = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(candidate, str) and re.fullmatch(r"[0-9A-Z]{5}", candidate):
            sqlstate = candidate
            break
        current = getattr(current, "orig", None) or getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    if sqlstate:
        category = {"55P03": "lock_timeout", "57014": "statement_timeout"}.get(sqlstate, "database_error")
        return {"error_code": "DATABASE_OPERATION_FAILED", "error_category": category, "sqlstate": sqlstate}
    return {"error_code": "CHILD_OPERATION_FAILED", "error_category": "timeout" if isinstance(exc, TimeoutError) else "runtime_error"}


def schema_snapshot(connection):
    import sqlalchemy as sa
    from deerflow.persistence.migrations._helpers import _normalize_default

    inspector = sa.inspect(connection)
    require(inspector.has_table("alembic_version"), "VERSION_TABLE_MISSING")
    revisions = list(connection.execute(sa.text("SELECT version_num FROM alembic_version LIMIT 8")).scalars())
    require(len(revisions) == 1 and revisions[0] in (BASE, TARGET), "REVISION_REFUSED")
    require(inspector.has_table("oa_policy_flow_jobs"), "OA_TABLE_MISSING")
    columns = inspector.get_columns("oa_policy_flow_jobs")
    require(len(columns) <= 64, "COLUMN_LIMIT")
    # Export only known names and type categories, never arbitrary expressions.
    require({column["name"] for column in columns} <= OLD_COLUMNS | {"result_protocol", "result_files"}, "OA_UNKNOWN_COLUMN")
    rendered = []
    for column in columns:
        typ = column["type"]
        type_name = next((name for name, cls in (("VARCHAR", sa.String), ("JSON", sa.JSON), ("INTEGER", sa.Integer), ("TIMESTAMP", sa.DateTime)) if isinstance(typ, cls)), "OTHER")
        default = _normalize_default(column.get("default"))
        rendered.append({"name": column["name"], "type": type_name, "length": getattr(typ, "length", None), "nullable": column["nullable"],
                         "default": "none" if default is None else "v1" if default == "'v1'" else "other"})
    version = connection.execute(sa.text("SHOW server_version_num")).scalar()
    require(str(version).isdigit(), "POSTGRES_VERSION_INVALID")
    return {"revisions": revisions, "columns": rendered, "postgres_version_num": int(version)}


def require_base(snapshot):
    require(snapshot["revisions"] == [BASE], "EXPANSION_REQUIRES_0020")
    require({column["name"] for column in snapshot["columns"]} == OLD_COLUMNS, "OA_BASE_COLUMNS_MISMATCH")


async def readonly_snapshot(engine, helper, *, verify=False):
    import sqlalchemy as sa
    async with engine.connect() as connection:
        async with connection.begin():
            await connection.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            snapshot = await connection.run_sync(schema_snapshot)
            if verify:
                require(snapshot["revisions"] == [TARGET], "VERIFY_REQUIRES_0021")
                accepted = await connection.run_sync(lambda conn: helper.accepts_oa_additive_schema(conn, local_head=BASE))
                require(accepted, "COMPATIBILITY_NOT_ACCEPTED")
                require({column["name"] for column in snapshot["columns"]} == OLD_COLUMNS | {"result_protocol", "result_files"}, "OA_EXPANDED_COLUMNS_MISMATCH")
                snapshot["compatibility_verified"] = True
            return snapshot


async def run(args, result):
    import sqlalchemy as sa
    import sqlalchemy.ext.asyncio as sqlalchemy_async
    from deerflow.config import get_app_config
    from deerflow.persistence import bootstrap, oa_schema_compatibility as helper
    from deerflow.persistence.migrations import _env_filters, _helpers

    action, pin = args["action"], args["pin"]
    require(action in ("inspect", "expand", "verify"), "ACTION_REFUSED")
    result.update(action=action, database_readonly=action != "expand")
    sources = {"bootstrap": sha(read_regular(Path(bootstrap.__file__))), "helper": sha(read_regular(Path(helper.__file__)))}
    require(sources == pin["sources"], "CONSUMER_SOURCE_HASH")
    result["source_hashes"] = sources
    installed_dir = bootstrap._MIGRATIONS_DIR
    old_tree, tree_digest = migration_tree(installed_dir, pin["migration_base_sources"])
    for name, module in (("_helpers.py", _helpers), ("_env_filters.py", _env_filters)):
        imported_path = Path(module.__file__)
        require(imported_path.resolve() == (installed_dir / name).resolve(), "IMPORTED_MIGRATION_SUPPORT_PATH")
        require(sha(read_regular(imported_path)) == pin["migration_base_sources"][name], "IMPORTED_MIGRATION_SUPPORT_HASH")
    result["migration_tree_sha256"] = tree_digest
    require(bootstrap._get_head_revision() == BASE, "INSTALLED_HEAD_CHANGED")
    result["migration_head"] = BASE
    config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")
    result["database_fingerprint"] = fingerprint(config.postgres_url)
    require(result["database_fingerprint"] == args["expected_database_fingerprint"], "DATABASE_FINGERPRINT_MISMATCH")
    url = sa.engine.make_url(config.app_sqlalchemy_url)
    require(url.drivername == "postgresql+asyncpg", "ASYNC_POSTGRES_REQUIRED")

    original_create = sqlalchemy_async.create_async_engine
    engine_count = 0

    def bounded_engine(candidate, **kwargs):
        nonlocal engine_count
        require(sa.engine.make_url(candidate) == url and not kwargs, "ENGINE_TARGET_OR_OPTIONS_CHANGED")
        engine_count += 1
        return original_create(candidate, echo=False, hide_parameters=True, poolclass=sa.pool.NullPool, connect_args={
            "timeout": 10, "command_timeout": 45, "server_settings": {"lock_timeout": str(LOCK_TIMEOUT_MS), "statement_timeout": str(STATEMENT_TIMEOUT_MS)},
        })

    sqlalchemy_async.create_async_engine = bounded_engine
    engine = None
    try:
        engine = bounded_engine(url)
        if action != "expand":
            snapshot = await readonly_snapshot(engine, helper, verify=action == "verify")
            if action == "inspect":
                require_base(snapshot)
            result.update(snapshot)
        else:
            require(args.get("expected_migration_tree_sha256") == tree_digest, "INSPECTED_MIGRATION_TREE_CHANGED")
            source = args["migration_source"].encode("utf-8")
            require(len(source) == 801 and b"\r" not in source and sha(source) == MIGRATION_SHA256, "ADDITIVE_MIGRATION_HASH")
            saved_lock, saved_upgrade = bootstrap._bootstrap_lock, bootstrap._upgrade
            saved_dir, saved_head = bootstrap._MIGRATIONS_DIR, bootstrap._HEAD_REVISION
            saved_stamp = bootstrap._stamp
            saved_decide = bootstrap._decide_state
            lock_held, dispatched = False, False

            @contextlib.asynccontextmanager
            async def gated_lock(target_engine, *, backend):
                nonlocal lock_held
                require(target_engine is engine and backend == "postgres", "BOOTSTRAP_TARGET_CHANGED")
                async with saved_lock(target_engine, backend=backend):
                    before = await readonly_snapshot(engine, helper)
                    require_base(before)
                    result["before"] = before
                    lock_held = True
                    try:
                        yield
                    finally:
                        lock_held = False

            def guarded_upgrade(cfg, revision):
                nonlocal dispatched
                require(lock_held and not dispatched and revision == "head", "UPGRADE_DISPATCH_REFUSED")
                require(sa.engine.make_url(cfg.get_main_option("sqlalchemy.url")) == url, "ALEMBIC_TARGET_CHANGED")
                require(Path(cfg.get_main_option("script_location")) == bootstrap._MIGRATIONS_DIR, "ALEMBIC_TREE_CHANGED")
                # Durable controller intent precedes invocation. Once dispatched,
                # even a returned error remains uncertain without external proof.
                dispatched = True
                result["migration_outcome"] = "uncertain"
                result["schema_migrated"] = None
                saved_upgrade(cfg, revision)

            def forbid_stamp(*unused, **ignored):
                raise Refused("STAMP_FORBIDDEN")

            def versioned_only(state):
                decision = saved_decide(state)
                require(lock_held and decision == "versioned", "NON_VERSIONED_DISPATCH_FORBIDDEN")
                return decision

            try:
                with tempfile.TemporaryDirectory(prefix="oa-schema-expansion-") as temporary:
                    staged = Path(temporary) / "migrations"
                    staged.mkdir()
                    for name, raw in old_tree.items():
                        destination = staged / name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(raw)
                    (staged / MIGRATION_NAME).write_bytes(source)
                    bootstrap._MIGRATIONS_DIR, bootstrap._HEAD_REVISION = staged, None
                    require(bootstrap._get_head_revision() == TARGET, "STAGED_HEAD_CHANGED")
                    bootstrap._bootstrap_lock, bootstrap._upgrade, bootstrap._stamp = gated_lock, guarded_upgrade, forbid_stamp
                    bootstrap._decide_state = versioned_only
                    # No wait_for: cancelling to_thread could release the advisory
                    # lock while Alembic was still executing. Server timeouts and
                    # the whole-process deadline provide the only time bounds.
                    await bootstrap.bootstrap_schema(engine, backend="postgres")
                    require(dispatched and engine_count == 2, "BOOTSTRAP_EXECUTION_SHAPE")
                    result.update(await readonly_snapshot(engine, helper, verify=True))
                    result.update(migration_outcome="committed", schema_migrated=True, migration_source_sha256=MIGRATION_SHA256,
                                  bounded_engine_count=engine_count)
            finally:
                bootstrap._MIGRATIONS_DIR, bootstrap._HEAD_REVISION = saved_dir, saved_head
                bootstrap._bootstrap_lock, bootstrap._upgrade, bootstrap._stamp = saved_lock, saved_upgrade, saved_stamp
                bootstrap._decide_state = saved_decide
        require(bootstrap._get_head_revision() == BASE, "INSTALLED_HEAD_CHANGED")
        require(migration_tree(installed_dir, pin["migration_base_sources"])[1] == tree_digest, "INSTALLED_MIGRATIONS_CHANGED")
        require({"bootstrap": sha(read_regular(Path(bootstrap.__file__))), "helper": sha(read_regular(Path(helper.__file__)))} == sources, "INSTALLED_SOURCES_CHANGED")
        result.update(status="PASS", installed_sources_unchanged=True, lock_timeout_ms=LOCK_TIMEOUT_MS, statement_timeout_ms=STATEMENT_TIMEOUT_MS)
    finally:
        if engine is not None:
            await engine.dispose()
        sqlalchemy_async.create_async_engine = original_create


def execute(args):
    result = {"status": "INCOMPLETE", "migration_outcome": "not_requested", "schema_migrated": False,
              "raw_errors_exported": False, "provider_requests": 0, "business_values_exported": False}
    try:
        asyncio.run(run(args, result))
    except Exception as exc:
        result.update(status="INCOMPLETE", **error_metadata(exc))
    return result


def main():
    result = {"status": "INCOMPLETE", "migration_outcome": "not_requested", "schema_migrated": False,
              "raw_errors_exported": False, "provider_requests": 0, "business_values_exported": False}

    def deadline():
        # Exit the whole process, including Alembic's worker thread. Disconnect
        # releases the unchanged advisory lock; never pretend this proved rollback.
        receipt = dict(result, status="INCOMPLETE", error_code="PROCESS_DEADLINE", error_category="timeout")
        try:
            os.write(1, (json.dumps(receipt, sort_keys=True) + "\n").encode())
        finally:
            os._exit(124)

    timer = threading.Timer(PROCESS_DEADLINE_SECONDS, deadline)
    timer.daemon = True
    timer.start()
    try:
        # Suppress application/config/SQL diagnostics, including arbitrarily large
        # errors, rather than buffering them or exporting credential-bearing text.
        logging.disable(logging.CRITICAL)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                require(len(sys.argv) == 2 and len(sys.argv[1]) <= 65536, "ARGUMENTS_INVALID")
                args = json.loads(sys.argv[1])
                asyncio.run(run(args, result))
            except Exception as exc:
                result.update(status="INCOMPLETE", **error_metadata(exc))
    finally:
        timer.cancel()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
