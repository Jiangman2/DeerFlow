"""Real PostgreSQL 16 qualification; this suite cannot use application config.

Run in the backend runtime image on an INTERNAL Docker network containing only
a postgres:16-alpine fixture aliased oa-schema-expansion-postgres. Export the
approved bootstrap/helper/migration sources as /oa-fixture-sources.json first.
No production URL is read or accepted, and each test owns a new fixture DB.
"""

import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/packages/harness"))
sys.path.insert(0, str(ROOT / "backend"))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from alembic.migration import MigrationContext
from alembic.operations import Operations

import deerflow.config
import deerflow.persistence
from deerflow.config.database_config import DatabaseConfig


ADMIN_URL = "postgresql+asyncpg://oa_fixture:oa_fixture@oa-schema-expansion-postgres:5432/postgres"
KNOWN_BOOTSTRAPS = {
    "gateway": "611dff501f59b3b94bccd4b3f9b31309955eb5e38356ed257882ead79d8e1fb1",
    "runner": "7b704c7f4434556d354f9bbbd326dfdb15f935dbb5b1d8b1fd7908866f02e612",
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


child = load("oa_schema_expansion_child", ROOT / "deploy/enterprise/oa-schema-expansion-child.py")


class PostgreSQLFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("OA_SCHEMA_EXPANSION_FIXTURE") != "postgres16-isolated":
            raise RuntimeError("Run only in the documented isolated PostgreSQL fixture")
        cls.sources = json.loads(Path("/oa-fixture-sources.json").read_text())
        cls.pins = json.loads((ROOT / "deploy/enterprise/oa-schema-expansion-migration-base-20261022.json").read_text())["files"]
        for role, digest in KNOWN_BOOTSTRAPS.items():
            assert child.sha(cls.sources[role].encode()) == digest
        assert child.sha(cls.sources["helper"].encode()) == "2dd04bfafc29856c0f646926043aed7b713d79d69744a9053ff99fdf6af24c8b"
        assert {name: child.sha(raw.encode()) for name, raw in cls.sources["migrations"].items()} == cls.pins
        cls.migration = (ROOT / "backend/packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py").read_text()
        assert child.sha(cls.migration.encode()) == child.MIGRATION_SHA256

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oa-child-fixture-")
        self.directory = Path(self.temporary.name)
        self.persistence = self.directory / "persistence"
        self.persistence.mkdir()
        for name, raw in self.sources["migrations"].items():
            destination = self.persistence / "migrations" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw.encode())
        (self.persistence / "oa_schema_compatibility.py").write_bytes(self.sources["helper"].encode())
        self.install("gateway")
        self.database = "oa_expansion_" + uuid.uuid4().hex
        self.url = ADMIN_URL.rsplit("/", 1)[0] + "/" + self.database
        self.config = SimpleNamespace(database=DatabaseConfig(backend="postgres", postgres_url=self.url))
        self.config_patch = patch.object(deerflow.config, "get_app_config", return_value=self.config)
        self.config_patch.start()
        asyncio.run(self.create_database())

    def install(self, role):
        (self.persistence / "bootstrap.py").write_bytes(self.sources[role].encode())
        load("deerflow.persistence.migrations._helpers", self.persistence / "migrations/_helpers.py")
        load("deerflow.persistence.migrations._env_filters", self.persistence / "migrations/_env_filters.py")
        self.helper = load("deerflow.persistence.oa_schema_compatibility", self.persistence / "oa_schema_compatibility.py")
        self.bootstrap = load("deerflow.persistence.bootstrap", self.persistence / "bootstrap.py")
        deerflow.persistence.bootstrap = self.bootstrap
        deerflow.persistence.oa_schema_compatibility = self.helper
        self.pin = {"sources": {"bootstrap": KNOWN_BOOTSTRAPS[role], "helper": child.sha(self.sources["helper"].encode())}, "migration_base_sources": self.pins}

    async def create_database(self):
        engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                version = int((await connection.execute(sa.text("SHOW server_version_num"))).scalar())
                self.assertTrue(160000 <= version < 170000)
                await connection.execute(sa.text('CREATE DATABASE "' + self.database + '"'))
        finally:
            await engine.dispose()
        engine = self.engine()
        try:
            async with engine.begin() as connection:
                await connection.run_sync(self.seed)
        finally:
            await engine.dispose()

    def seed(self, connection):
        # The real existing OA migration creates the old column/index shape.
        connection.execute(sa.text("CREATE TABLE users (id VARCHAR(64) PRIMARY KEY)"))
        context = MigrationContext.configure(connection)
        module = runpy.run_path(str(self.persistence / "migrations/versions/0014_oa_policy_flow_jobs.py"))
        with Operations.context(context):
            module["upgrade"]()
        connection.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        connection.execute(sa.text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": child.BASE})
        connection.execute(sa.text("INSERT INTO users VALUES ('fixture-owner')"))
        table = sa.Table("oa_policy_flow_jobs", sa.MetaData(), autoload_with=connection)
        self.legacy = {"id": "legacy", "idempotency_key_hash": "a" * 64, "request_id": "fixture-request", "request_fingerprint": "b" * 64,
                       "owner_user_id": "fixture-owner", "assistant_id": "fixture-assistant", "thread_id": "fixture-thread", "status": "succeeded",
                       "callback_status": "disabled", "document_filename": "synthetic.pdf", "key_fields": {}, "process_attempts": 1, "callback_attempts": 0,
                       "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}
        connection.execute(table.insert().values(**self.legacy))

    def tearDown(self):
        self.config_patch.stop()
        asyncio.run(self.drop_database())
        self.temporary.cleanup()

    async def drop_database(self):
        engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(sa.text('DROP DATABASE "' + self.database + '" WITH (FORCE)'))
        finally:
            await engine.dispose()

    def engine(self):
        return create_async_engine(self.url, poolclass=sa.pool.NullPool)

    def arguments(self, action="expand"):
        return {"action": action, "pin": copy.deepcopy(self.pin), "expected_database_fingerprint": child.fingerprint(self.url),
                "migration_source": self.migration,
                "expected_migration_tree_sha256": child.sha(json.dumps(self.pins, sort_keys=True).encode())}

    async def invoke(self, action="expand", *, args=None):
        result = {"status": "INCOMPLETE", "migration_outcome": "not_requested", "schema_migrated": False, "provider_requests": 0,
                  "raw_errors_exported": False, "business_values_exported": False}
        try:
            await child.run(args or self.arguments(action), result)
        except Exception as exc:
            result.update(status="INCOMPLETE", **child.error_metadata(exc))
        return result

    async def sql(self, statement):
        engine = self.engine()
        try:
            async with engine.begin() as connection:
                result = await connection.execute(sa.text(statement))
                return result.fetchall() if result.returns_rows else None
        finally:
            await engine.dispose()

    async def assert_base(self):
        engine = self.engine()
        try:
            snapshot = await child.readonly_snapshot(engine, self.helper)
            child.require_base(snapshot)
            self.assertEqual(await self.sql("SELECT count(*) FROM oa_policy_flow_jobs WHERE id='legacy'"), [(1,)])
        finally:
            await engine.dispose()

    def test_success_preserves_legacy_row_default_and_installed_tree(self):
        async def scenario():
            before = child.migration_tree(self.bootstrap._MIGRATIONS_DIR, self.pins)
            result = await self.invoke()
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(result["migration_outcome"], "committed")
            self.assertEqual(result["bounded_engine_count"], 2)
            self.assertEqual(result["revisions"], [child.TARGET])
            self.assertEqual(result["migration_head"], child.BASE)
            self.assertTrue(result["compatibility_verified"])
            self.assertEqual(await self.sql("SELECT result_protocol, result_files FROM oa_policy_flow_jobs WHERE id='legacy'"), [("v1", None)])
            self.assertEqual(await self.sql("SELECT id, document_filename, process_attempts FROM oa_policy_flow_jobs"), [("legacy", "synthetic.pdf", 1)])
            self.assertEqual(child.migration_tree(self.bootstrap._MIGRATIONS_DIR, self.pins), before)
            self.assertFalse((self.bootstrap._MIGRATIONS_DIR / child.MIGRATION_NAME).exists())
            self.assertEqual(self.bootstrap._get_head_revision(), child.BASE)
        asyncio.run(scenario())

    def test_both_exact_old_consumers_bootstrap_and_verify_expanded_schema(self):
        async def scenario():
            self.assertEqual((await self.invoke())["status"], "PASS")
            for role in KNOWN_BOOTSTRAPS:
                self.install(role)
                result = await self.invoke("verify")
                self.assertEqual(result["status"], "PASS", result)
                self.assertTrue(result["database_readonly"])
                engine = self.engine()
                statements = []
                sa.event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, sql, params, context, executemany: statements.append(sql))
                try:
                    await self.bootstrap.bootstrap_schema(engine, backend="postgres")
                finally:
                    await engine.dispose()
                self.assertFalse(any(sql.lstrip().upper().startswith(("ALTER", "CREATE", "INSERT", "UPDATE", "DELETE")) for sql in statements))
                self.assertEqual(self.bootstrap._get_head_revision(), child.BASE)
        asyncio.run(scenario())

    def test_inspect_uses_readonly_transaction_and_exports_metadata_only(self):
        async def scenario():
            with patch.object(self.bootstrap, "bootstrap_schema", side_effect=AssertionError("bootstrap must not run")):
                result = await self.invoke("inspect")
            self.assertEqual(result["status"], "PASS", result)
            self.assertTrue(result["database_readonly"])
            self.assertEqual(result["revisions"], [child.BASE])
            self.assertEqual(result["migration_outcome"], "not_requested")
            serialized = json.dumps(result)
            self.assertNotIn("fixture-owner", serialized)
            self.assertNotIn("oa_fixture", serialized)
            self.assertNotIn("synthetic.pdf", serialized)
            await self.assert_base()
        asyncio.run(scenario())

    def test_advisory_lock_contention_is_bounded_before_dispatch(self):
        async def scenario():
            engine = self.engine()
            try:
                async with engine.connect() as holder:
                    await holder.execute(sa.text("SELECT pg_advisory_lock(:key)"), {"key": self.bootstrap._PG_LOCK_KEY})
                    await holder.commit()
                    started = time.monotonic()
                    with patch.object(child, "LOCK_TIMEOUT_MS", 200):
                        result = await self.invoke()
                    self.assertLess(time.monotonic() - started, 6)
                    self.assertEqual(result["status"], "INCOMPLETE")
                    self.assertEqual(result.get("sqlstate"), "55P03", result)
                    self.assertEqual(result["migration_outcome"], "not_requested")
                    await holder.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": self.bootstrap._PG_LOCK_KEY})
            finally:
                await engine.dispose()
            await self.assert_base()
        asyncio.run(scenario())

    def test_alembic_table_lock_timeout_rolls_back_but_receipt_stays_uncertain(self):
        async def scenario():
            engine = self.engine()
            try:
                async with engine.connect() as holder:
                    async with holder.begin():
                        await holder.execute(sa.text("LOCK TABLE oa_policy_flow_jobs IN ROW SHARE MODE"))
                        started = time.monotonic()
                        with patch.object(child, "LOCK_TIMEOUT_MS", 200):
                            result = await self.invoke()
                        self.assertLess(time.monotonic() - started, 6)
                        self.assertEqual(result.get("sqlstate"), "55P03", result)
                        self.assertEqual(result["migration_outcome"], "uncertain")
                        self.assertIsNone(result["schema_migrated"])
            finally:
                await engine.dispose()
            await self.assert_base()
        asyncio.run(scenario())

    def test_actual_alembic_connection_statement_timeout_rolls_back_both_columns(self):
        async def scenario():
            # A fixture-only event trigger sleeps after actual ADD COLUMN DDL.
            # This proves timeout reaches Alembic's separate connection and that
            # completed first-column DDL is transactional, not a partial publish.
            await self.sql("CREATE FUNCTION oa_fixture_delay() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(2); END; $$")
            await self.sql("CREATE EVENT TRIGGER oa_fixture_delay_trigger ON ddl_command_end WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION oa_fixture_delay()")
            with patch.object(child, "STATEMENT_TIMEOUT_MS", 200):
                result = await self.invoke()
            self.assertEqual(result.get("sqlstate"), "57014", result)
            self.assertEqual(result["migration_outcome"], "uncertain")
            await self.assert_base()
        asyncio.run(scenario())

    def test_wrong_revision_refused_without_dispatch(self):
        async def scenario():
            await self.sql("UPDATE alembic_version SET version_num='0022_unknown'")
            with patch.object(self.bootstrap, "_upgrade", side_effect=AssertionError("must not dispatch")):
                result = await self.invoke()
            self.assertEqual(result.get("error_code"), "REVISION_REFUSED", result)
            self.assertEqual(result["migration_outcome"], "not_requested")
            self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [("0022_unknown",)])
        asyncio.run(scenario())

    def test_partial_columns_refused_without_dispatch(self):
        async def scenario():
            await self.sql("ALTER TABLE oa_policy_flow_jobs ADD COLUMN result_protocol VARCHAR(8) NOT NULL DEFAULT 'v1'")
            with patch.object(self.bootstrap, "_upgrade", side_effect=AssertionError("must not dispatch")):
                result = await self.invoke()
            self.assertEqual(result.get("error_code"), "OA_BASE_COLUMNS_MISMATCH", result)
            self.assertEqual(result["migration_outcome"], "not_requested")
            self.assertEqual(await self.sql("SELECT version_num FROM alembic_version"), [(child.BASE,)])
        asyncio.run(scenario())

    def test_empty_and_legacy_schemas_cannot_create_or_stamp(self):
        async def scenario():
            await self.sql("DROP TABLE alembic_version")
            for empty in (False, True):
                if empty:
                    await self.sql("DROP TABLE oa_policy_flow_jobs")
                    await self.sql("DROP TABLE users")
                with patch.object(self.bootstrap, "_run_create_all_sync", side_effect=AssertionError("must not create")), \
                     patch.object(self.bootstrap, "_run_baseline_create_all_sync", side_effect=AssertionError("must not create")):
                    result = await self.invoke()
                self.assertEqual(result.get("error_code"), "VERSION_TABLE_MISSING", result)
                self.assertEqual(result["migration_outcome"], "not_requested")
        asyncio.run(scenario())

    def test_second_expand_is_refused_and_verify_does_not_write(self):
        async def scenario():
            self.assertEqual((await self.invoke())["status"], "PASS")
            result = await self.invoke()
            self.assertEqual(result.get("error_code"), "EXPANSION_REQUIRES_0020", result)
            self.assertEqual(result["migration_outcome"], "not_requested")
            self.assertEqual((await self.invoke("verify"))["status"], "PASS")
        asyncio.run(scenario())

    def test_verify_rejects_wrong_column_shape(self):
        async def scenario():
            self.assertEqual((await self.invoke())["status"], "PASS")
            await self.sql("ALTER TABLE oa_policy_flow_jobs ALTER COLUMN result_protocol TYPE VARCHAR(9)")
            result = await self.invoke("verify")
            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertEqual(result["migration_outcome"], "not_requested")
        asyncio.run(scenario())

    def test_pin_fingerprint_and_migration_drift_refused_without_dispatch(self):
        async def scenario():
            cases = []
            wrong = self.arguments(); wrong["pin"]["sources"]["bootstrap"] = "0" * 64; cases.append((wrong, "CONSUMER_SOURCE_HASH"))
            wrong = self.arguments(); wrong["expected_database_fingerprint"] = "0" * 64; cases.append((wrong, "DATABASE_FINGERPRINT_MISMATCH"))
            wrong = self.arguments(); wrong["migration_source"] += "\n"; cases.append((wrong, "ADDITIVE_MIGRATION_HASH"))
            wrong = self.arguments(); wrong["expected_migration_tree_sha256"] = "0" * 64; cases.append((wrong, "INSPECTED_MIGRATION_TREE_CHANGED"))
            for arguments, code in cases:
                result = await self.invoke(args=arguments)
                self.assertEqual(result.get("error_code"), code, result)
                self.assertEqual(result["migration_outcome"], "not_requested")
            await self.assert_base()
        asyncio.run(scenario())

    def test_symlink_and_unexpected_migration_refused(self):
        async def scenario():
            unexpected = self.persistence / "migrations/versions/0099_unknown.py"
            unexpected.write_text("revision='0099_unknown'\n")
            result = await self.invoke()
            self.assertEqual(result.get("error_code"), "UNEXPECTED_MIGRATION_SOURCE", result)
            unexpected.unlink()
            unexpected.symlink_to(self.persistence / "bootstrap.py")
            result = await self.invoke()
            self.assertEqual(result.get("error_code"), "MIGRATION_TREE_SYMLINK", result)
            unexpected.unlink()
            await self.assert_base()
        asyncio.run(scenario())

    def test_sanitization_never_serializes_exception_text(self):
        result = child.error_metadata(RuntimeError("password=synthetic-secret postgresql://user:credential@private"))
        self.assertEqual(result, {"error_code": "CHILD_OPERATION_FAILED", "error_category": "runtime_error"})
        self.assertNotIn("synthetic", json.dumps(result))

    def subprocess_child(self, action, deadline=120):
        # Only fixture metadata and this deliberately non-secret fixture DSN are
        # passed; the subprocess never loads the real get_app_config result.
        wrapper = """
import runpy,sys
from pathlib import Path
from types import SimpleNamespace
n=runpy.run_path(sys.argv[1],run_name='fixture_loader')
load=n['load']; persistence=Path(sys.argv[2])
load('deerflow.persistence.migrations._helpers',persistence/'migrations/_helpers.py')
load('deerflow.persistence.migrations._env_filters',persistence/'migrations/_env_filters.py')
helper=load('deerflow.persistence.oa_schema_compatibility',persistence/'oa_schema_compatibility.py')
bootstrap=load('deerflow.persistence.bootstrap',persistence/'bootstrap.py')
import deerflow.config,deerflow.persistence,deerflow.persistence.models
deerflow.persistence.bootstrap=bootstrap
deerflow.persistence.oa_schema_compatibility=helper
fixture_config=SimpleNamespace(database=n['DatabaseConfig'](backend='postgres',postgres_url=sys.argv[3]))
deerflow.config.get_app_config=lambda: fixture_config
child=n['child']; child.PROCESS_DEADLINE_SECONDS=float(sys.argv[4])
arguments=sys.argv[5]; sys.argv=['fixture-child',arguments]
raise SystemExit(child.main())
"""
        return subprocess.run([sys.executable, "-I", "-B", "-c", wrapper, str(Path(__file__)), str(self.persistence), self.url,
                               str(deadline), json.dumps(self.arguments(action))], text=True, capture_output=True, timeout=45)

    def test_isolated_main_outputs_one_sanitized_inspect_receipt(self):
        completed = self.subprocess_child("inspect")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.strip().splitlines()), 1)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["revisions"], [child.BASE])
        self.assertNotIn("oa_fixture", completed.stdout)

    def test_hard_deadline_kills_alembic_thread_releases_lock_and_rolls_back(self):
        async def scenario():
            await self.sql("CREATE FUNCTION oa_fixture_delay() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(12); END; $$")
            await self.sql("CREATE EVENT TRIGGER oa_fixture_delay_trigger ON ddl_command_end WHEN TAG IN ('ALTER TABLE') EXECUTE FUNCTION oa_fixture_delay()")
            started = time.monotonic()
            completed = self.subprocess_child("expand", deadline=3)
            # The wrapper imports the complete installed model registry before
            # main starts its three-second timer; cold bind-mount imports on
            # Docker Desktop can dominate that fixture-only setup cost.
            self.assertLess(time.monotonic() - started, 40)
            self.assertEqual(completed.returncode, 124, completed.stderr)
            self.assertEqual(completed.stderr, "")
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["error_code"], "PROCESS_DEADLINE")
            self.assertEqual(receipt["migration_outcome"], "uncertain")
            # The server might finish its interrupted backend at the socket-close
            # boundary; this read waits for that bounded cleanup, never retries DDL.
            await self.assert_base()
            engine = self.engine()
            try:
                async with engine.connect() as connection:
                    acquired = await connection.execute(sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": self.bootstrap._PG_LOCK_KEY})
                    self.assertTrue(acquired.scalar())
                    await connection.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": self.bootstrap._PG_LOCK_KEY})
            finally:
                await engine.dispose()
        asyncio.run(scenario())

    def test_hard_deadline_terminates_worker_thread_without_importing_application(self):
        wrapper = """
import asyncio,os,runpy,sys,time
n=runpy.run_path(sys.argv[1],run_name='isolated_child_test')
async def blocked_run(arguments,result):
    def worker():
        result.update(migration_outcome='uncertain',thread_started=True)
        time.sleep(30)
    await asyncio.to_thread(worker)
n['main'].__globals__.update(run=blocked_run,PROCESS_DEADLINE_SECONDS=0.5)
if len(sys.argv)>2: os.close(1)
sys.argv=['synthetic-child','{}']
raise SystemExit(n['main']())
"""
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-I", "-B", "-c", wrapper, str(ROOT / "deploy/enterprise/oa-schema-expansion-child.py")],
                                text=True, capture_output=True, timeout=5)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["thread_started"])
        self.assertEqual(receipt["error_code"], "PROCESS_DEADLINE")
        self.assertEqual(receipt["migration_outcome"], "uncertain")
        disconnected = subprocess.run([sys.executable, "-I", "-B", "-c", wrapper, str(ROOT / "deploy/enterprise/oa-schema-expansion-child.py"), "closed-stdout"],
                                      text=True, capture_output=True, timeout=5)
        self.assertEqual(disconnected.returncode, 124, disconnected.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
