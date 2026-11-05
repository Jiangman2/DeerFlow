"""Offline tests against exact feature/base source fixtures in the runtime image.

Mount a pinned fixture at /oa-feature-fixture (backend/ plus pins.json) and run
with --network none. The fixture contains no application configuration or keys.
"""

import asyncio
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


HERE = Path(__file__).resolve().parent
FIXTURE = Path(os.environ.get("OA_FEATURE_LIVE_FIXTURE_DIR", "/oa-feature-fixture"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


child = load("oa_feature_live_check", HERE / "oa-feature-live-check.py")


class MetadataConnection:
    def __init__(self):
        self.queries = []
        self.options = None
        self.closed = False
        self.revision = child.REVISION
        self.columns = [
            {"column_name": "result_protocol", "data_type": "character varying", "character_maximum_length": 8, "is_nullable": "NO", "column_default": "'v1'::character varying"},
            {"column_name": "result_files", "data_type": "json", "character_maximum_length": None, "is_nullable": "YES", "column_default": None},
        ]

    @contextlib.asynccontextmanager
    async def transaction(self, **options):
        self.options = options
        yield

    async def fetch(self, sql):
        self.queries.append(sql)
        return [(self.revision,)] if "alembic_version" in sql else self.columns

    async def close(self, **kwargs):
        self.closed = True


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("OA_FEATURE_LIVE_FIXTURE") != "offline-exact-sources":
            raise RuntimeError("Use the isolated, exact-source fixture with --network none")
        cls.root = FIXTURE / "backend"
        cls.pin = json.loads((FIXTURE / "pins.json").read_text())
        runtime_backend = Path(os.environ.get("OA_FEATURE_RUNTIME_BACKEND", "/app/backend"))
        sys.path.insert(0, str(runtime_backend))
        if os.name == "nt":
            sys.path.insert(0, str(runtime_backend / "packages/harness"))
        import deerflow.config
        import deerflow.persistence
        import app.gateway
        cls.config_module = deerflow.config
        cls.oa = load("app.gateway.oa_policy_flow_config", cls.root / "app/gateway/oa_policy_flow_config.py")
        app.gateway.oa_policy_flow_config = cls.oa
        cls.bootstrap = load("deerflow.persistence.bootstrap", cls.root / child.BOOTSTRAP)
        deerflow.persistence.bootstrap = cls.bootstrap
        child.verify_sources(cls.pin, cls.root)

    def setUp(self):
        self.actual = SimpleNamespace(scheduler=SimpleNamespace(recursion_limit=300), max_recursion_limit=2000,
                                      tools=[SimpleNamespace(name=name, use="deerflow.community.appforge.tools:" + name + "_tool") for name in sorted(child.DATALAKE_NAMES)],
                                      database=SimpleNamespace(backend="postgres", postgres_url="postgresql+asyncpg://synthetic:fixture@127.0.0.1:5432/fixture"))
        self.config_patch = patch.object(self.config_module, "get_app_config", return_value=self.actual)
        self.config_patch.start()
        self.environment_patch = patch.dict(os.environ, {"OA_POLICY_FLOW_ENABLED": "false", "OA_POLICY_FLOW_MULTI_RESULT_ENABLED": "false", "OA_POLICY_FLOW_RECURSION_LIMIT": "1000"})
        self.environment_patch.start()
        self.health_patch = patch.object(child, "health", return_value=200)
        self.health_mock = self.health_patch.start()

    def tearDown(self):
        self.health_patch.stop()
        self.environment_patch.stop()
        self.config_patch.stop()

    def args(self, action="runtime"):
        return {"action": action, "pin": copy.deepcopy(self.pin), "expected_database_fingerprint": child.database_fingerprint(self.actual.database.postgres_url)}

    def test_exact_installed_runtime_configuration_and_progress_pass(self):
        with patch.object(self.bootstrap, "bootstrap_schema", side_effect=AssertionError("MUST_NOT_BOOTSTRAP")):
            result = child.runtime(self.args(), self.root)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["sources_verified"])
        self.assertEqual(len(result["source_hashes"]), 24)
        self.assertEqual(result["migration_head"], child.REVISION)
        self.assertEqual((result["scheduler_budget"], result["oa_budget"], result["global_budget"]), (300, 1000, 2000))
        self.assertFalse(result["v2_enabled"])
        self.assertEqual(result["datalake_tools"], ["datalake_data_qa", "datalake_smart_qa"])
        self.assertTrue(result["progress_contract_verified"])
        self.assertTrue(result["glm_guard_verified"])
        self.health_mock.assert_called_once_with()
        self.assertNotIn("synthetic", json.dumps(result))

    def test_v2_opt_in_drift_fails(self):
        with patch.dict(os.environ, {"OA_POLICY_FLOW_MULTI_RESULT_ENABLED": "true"}):
            with self.assertRaisesRegex(child.Refused, "V2_OPT_IN_CHANGED"):
                child.runtime(self.args(), self.root)
        self.health_mock.assert_not_called()

    def test_scheduler_oa_and_global_budgets_fail_independently(self):
        for attribute, value in (("scheduler", SimpleNamespace(recursion_limit=1000)), ("max_recursion_limit", 1000)):
            with patch.object(self.actual, attribute, value):
                with self.assertRaisesRegex(child.Refused, "SCOPED_BUDGET_CHANGED"):
                    child.runtime(self.args(), self.root)
        with patch.dict(os.environ, {"OA_POLICY_FLOW_RECURSION_LIMIT": "300"}):
            with self.assertRaisesRegex(child.Refused, "SCOPED_BUDGET_CHANGED"):
                child.runtime(self.args(), self.root)

    def test_feature_source_and_preserved_glm_drift_fail(self):
        for family, name in (("sources", next(iter(self.pin["sources"]))),
                             ("preserved_sources", child.PREFIX + "packages/harness/deerflow/models/glm_stream_guard.py")):
            pin = copy.deepcopy(self.pin)
            pin[family][name]["sha256"] = "0" * 64
            with self.assertRaisesRegex(child.Refused, "INSTALLED_SOURCE_CHANGED"):
                child.verify_sources(pin, self.root)

    def test_source_sets_and_baseline_bootstrap_are_mandatory(self):
        pin = copy.deepcopy(self.pin)
        pin["sources"].pop(next(iter(pin["sources"])))
        with self.assertRaisesRegex(child.Refused, "FEATURE_SOURCE_SET"):
            child.verify_sources(pin, self.root)
        pin = copy.deepcopy(self.pin)
        pin["baseline_bootstrap_sha256"] = "0" * 64
        with self.assertRaisesRegex(child.Refused, "BASE_BOOTSTRAP_PIN"):
            child.verify_sources(pin, self.root)

    def test_source_path_escape_is_refused(self):
        pin = copy.deepcopy(self.pin)
        pin["preserved_sources"][child.PREFIX + "../../outside.py"] = {"bytes": 1, "sha256": "0" * 64}
        with self.assertRaisesRegex(child.Refused, "SOURCE_PATH_INVALID"):
            child.verify_sources(pin, self.root)

    def test_retired_duplicate_and_wrong_export_registration_refused(self):
        sources, _ = child.verify_sources(self.pin, self.root)
        original = list(self.actual.tools)
        self.actual.tools.append(SimpleNamespace(name="datalake_detail_qa", use="retired"))
        with self.assertRaisesRegex(child.Refused, "DATALAKE_CONFIG_REGISTRATION_CHANGED"):
            child.registry_check(sources[child.REGISTRY], self.actual)
        self.actual.tools = original + [original[0]]
        with self.assertRaisesRegex(child.Refused, "DATALAKE_CONFIG_REGISTRATION_CHANGED"):
            child.registry_check(sources[child.REGISTRY], self.actual)
        self.actual.tools = original
        self.actual.tools[0].use = "unexpected:tool"
        with self.assertRaisesRegex(child.Refused, "DATALAKE_CONFIG_EXPORT_CHANGED"):
            child.registry_check(sources[child.REGISTRY], self.actual)

    def test_independent_md_search_registration_remains_allowed(self):
        sources, _ = child.verify_sources(self.pin, self.root)
        self.actual.tools.append(SimpleNamespace(name="datalake_md_search", use="deerflow.community.appforge.tools:datalake_md_search_tool"))
        self.assertEqual(child.registry_check(sources[child.REGISTRY], self.actual), sorted(child.DATALAKE_NAMES))

    def test_registry_and_glm_hook_ast_drift_refused(self):
        sources, _ = child.verify_sources(self.pin, self.root)
        with self.assertRaisesRegex(child.Refused, "DATALAKE_REGISTRY_CHANGED"):
            child.registry_check(b"DATALAKE_TOOL_NAMES=frozenset({'datalake_smart_qa'})", self.actual)
        changed = dict(sources)
        changed["packages/harness/deerflow/models/enterprise_glm.py"] = b"class EnterpriseGLMChat:\n    pass\n"
        with self.assertRaisesRegex(child.Refused, "GLM_STREAM_HOOK_MISSING"):
            child.glm_check(changed, self.root)
        del changed["packages/harness/deerflow/models/glm_stream_guard.py"]
        with self.assertRaisesRegex(child.Refused, "GLM_SOURCE_PINS_REQUIRED"):
            child.glm_check(changed, self.root)

    def test_wrong_installed_head_rejected_without_bootstrap(self):
        with patch.object(self.bootstrap, "_get_head_revision", return_value="0020_ppt_confirmation"):
            with self.assertRaisesRegex(child.Refused, "INSTALLED_HEAD_CHANGED"):
                child.runtime(self.args(), self.root)

    def test_offline_config_cannot_make_network_requests(self):
        def unexpected_network():
            socket.create_connection(("127.0.0.1", 1))
        with patch.object(self.config_module, "get_app_config", side_effect=unexpected_network):
            with self.assertRaisesRegex(child.Refused, "OFFLINE_CHECK_NETWORK_FORBIDDEN"):
                child.runtime(self.args(), self.root)

    def test_database_is_readonly_bounded_and_only_reads_schema_metadata(self):
        connection = MetadataConnection()
        connector = AsyncMock(return_value=connection)
        with patch("asyncpg.connect", connector):
            result = asyncio.run(child.database(self.args("database"), self.root))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["revisions"], [child.REVISION])
        self.assertTrue(result["database_readonly"])
        self.assertTrue(connection.closed)
        self.assertEqual(connection.options, {"readonly": True, "isolation": "repeatable_read"})
        self.assertEqual(len(connection.queries), 2)
        self.assertIn("information_schema.columns", connection.queries[1])
        self.assertNotIn("SELECT *", " ".join(connection.queries))
        self.assertEqual(connector.await_args.kwargs, {"timeout": 5, "command_timeout": 10, "server_settings": {"statement_timeout": "10000", "lock_timeout": "2000"}})
        self.assertNotIn("synthetic", json.dumps(result))

    def test_wrong_database_fingerprint_never_connects(self):
        args = self.args("database")
        args["expected_database_fingerprint"] = "0" * 64
        with patch("asyncpg.connect", AsyncMock()) as connector:
            with self.assertRaisesRegex(child.Refused, "DATABASE_FINGERPRINT_MISMATCH"):
                asyncio.run(child.database(args, self.root))
        connector.assert_not_called()

    def test_unknown_database_revision_and_partial_shape_fail_and_close(self):
        for mode in ("revision", "missing", "length", "default", "nullable", "files", "jsonb"):
            connection = MetadataConnection()
            if mode == "revision":
                connection.revision = "0022_unknown"
            elif mode == "missing":
                connection.columns.pop()
            elif mode == "length":
                connection.columns[0]["character_maximum_length"] = 9
            elif mode == "default":
                connection.columns[0]["column_default"] = "'v2'::character varying"
            elif mode == "nullable":
                connection.columns[0]["is_nullable"] = "YES"
            else:
                connection.columns[1]["data_type"] = "jsonb" if mode == "jsonb" else "text"
            with self.subTest(mode=mode), patch("asyncpg.connect", AsyncMock(return_value=connection)):
                with self.assertRaises(child.Refused):
                    asyncio.run(child.database(self.args("database"), self.root))
                self.assertTrue(connection.closed)

    def test_health_uses_fixed_direct_url_no_redirect_and_bounded_body(self):
        self.health_patch.stop()
        response = SimpleNamespace(status=200, geturl=lambda: "http://127.0.0.1:8001/health", read=lambda limit: b'{"status":"ok"}')
        context = contextlib.nullcontext(response)
        opener = SimpleNamespace(open=lambda url, timeout: context)
        with patch.object(child.urllib.request, "build_opener", return_value=opener) as build:
            self.assertEqual(child.health(), 200)
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], child.NoRedirect)
        with self.assertRaisesRegex(child.Refused, "HEALTH_REDIRECT_FORBIDDEN"):
            child.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.invalid")
        response.read = lambda limit: b"x" * 16385
        with patch.object(child.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(child.Refused, "LOCAL_HEALTH_RESPONSE"):
                child.health()
        self.health_patch.start()

    def test_main_receipt_has_required_safety_flags_and_sanitizes_errors(self):
        for succeeds in (True, False):
            sink = io.StringIO()
            with patch.object(sys, "argv", ["check", json.dumps({"action": "runtime"})]), \
                 patch.object(child, "runtime", return_value={"status": "PASS"}, side_effect=None if succeeds else RuntimeError("private secret")), \
                 contextlib.redirect_stdout(sink):
                code = child.main()
            result = json.loads(sink.getvalue())
            self.assertEqual(code, 0 if succeeds else 1)
            self.assertEqual(result["provider_requests"], 0)
            self.assertEqual(result["oa_callback_requests"], 0)
            self.assertFalse(result["configuration_modified"])
            self.assertFalse(result["schema_migrated"])
            self.assertNotIn("private secret", sink.getvalue())

    def test_deadline_stops_whole_child_even_with_closed_stdout(self):
        wrapper = """
import os,runpy,sys,time
n=runpy.run_path(sys.argv[1],run_name='fixture_child')
n['main'].__globals__.update(runtime=lambda args: time.sleep(30),DEADLINE_SECONDS=0.3)
if len(sys.argv)>2: os.close(1)
sys.argv=['child','{"action":"runtime"}']
raise SystemExit(n['main']())
"""
        for closed in (False, True):
            startup_bound = 20 if os.name == "nt" else 5
            started = time.monotonic()
            completed = subprocess.run([sys.executable, "-I", "-B", "-c", wrapper, str(HERE / "oa-feature-live-check.py")] + (["closed"] if closed else []),
                                       text=True, capture_output=True, timeout=startup_bound)
            self.assertLess(time.monotonic() - started, startup_bound)
            self.assertEqual(completed.returncode, 124, completed.stderr)
            if not closed:
                self.assertEqual(json.loads(completed.stdout)["error_code"], "PROCESS_DEADLINE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
