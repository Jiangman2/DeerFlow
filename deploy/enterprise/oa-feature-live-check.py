"""Bounded, read-only acceptance inside an installed OA feature Gateway.

No application startup, worker, model request, OA submission or schema bootstrap.
The controller pins the image; this child additionally binds its source files.
"""

import ast
import asyncio
import contextlib
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import sys
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import urllib.request
from unittest.mock import patch


ROOT = Path("/app/backend")
PREFIX = "app/backend/"
REVISION = "0021_oa_result_sets"
DEADLINE_SECONDS = 30
TAIL_SOURCES = frozenset(PREFIX + name for name in (
    "app/gateway/oa_policy_flow_config.py", "app/gateway/oa_policy_flow_results.py", "app/gateway/oa_policy_flow_service.py",
    "app/gateway/routers/oa_policy_flow.py", "app/gateway/services.py", "packages/harness/deerflow/community/appforge/data_qa_progress.py",
    "packages/harness/deerflow/community/appforge/data_qa_stream.py", "packages/harness/deerflow/community/appforge/tools.py",
    "packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py", "packages/harness/deerflow/persistence/oa_policy_flow_jobs/model.py",
))
REGISTRY = "packages/harness/deerflow/tools/tools.py"
BOOTSTRAP = "packages/harness/deerflow/persistence/bootstrap.py"
BASE_BOOTSTRAP_SHA256 = "b852ea3bd215124e605e8c5a276c1cc99343a67d4165136121d885ea3def9449"
GLM_FILES = ("enterprise_glm.py", "glm_stream_guard.py", "output_safety.py", "robust_openai.py")
DATALAKE_NAMES = frozenset({"datalake_smart_qa", "datalake_data_qa"})
RETIRED_NAMES = frozenset({"datalake_metrics_qa", "datalake_detail_qa", "datalake_metric_qa"})


class Refused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def regular_read(path):
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink() and before.st_size <= 262144, "SOURCE_FILE_UNSAFE")
    with path.open("rb") as stream:
        raw = stream.read(262145)
        after = os.fstat(stream.fileno())
    require(len(raw) <= 262144 and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "SOURCE_CHANGED_DURING_READ")
    return raw


def verify_sources(pin, root=ROOT):
    sources = pin["sources"]
    preserved = pin.get("preserved_sources", {})
    require(isinstance(sources, dict) and set(sources) == TAIL_SOURCES, "FEATURE_SOURCE_SET")
    require(isinstance(preserved, dict) and len(preserved) <= 32 and not set(sources) & set(preserved), "PRESERVED_SOURCE_SET")
    require(PREFIX + REGISTRY in preserved, "PRESERVED_REGISTRY_REQUIRED")
    raw_sources, hashes = {}, {}
    for name, expected in dict(preserved, **sources).items():
        require(isinstance(name, str) and name.startswith(PREFIX) and "\\" not in name, "SOURCE_PATH_INVALID")
        relative = PurePosixPath(name[len(PREFIX):])
        require(not relative.is_absolute() and ".." not in relative.parts and relative.suffix == ".py", "SOURCE_PATH_INVALID")
        path = root.joinpath(*relative.parts)
        require(path.resolve().is_relative_to(root.resolve()), "SOURCE_PATH_ESCAPE")
        raw = regular_read(path)
        require(isinstance(expected, dict) and len(raw) == expected["bytes"] and sha(raw) == expected["sha256"], "INSTALLED_SOURCE_CHANGED")
        raw_sources[str(relative)] = raw
        hashes[name] = sha(raw)
    require(pin.get("baseline_bootstrap_sha256") == BASE_BOOTSTRAP_SHA256, "BASE_BOOTSTRAP_PIN")
    bootstrap = regular_read(root / BOOTSTRAP)
    require(sha(bootstrap) == BASE_BOOTSTRAP_SHA256, "BASE_BOOTSTRAP_CHANGED")
    raw_sources[BOOTSTRAP] = bootstrap
    hashes[PREFIX + BOOTSTRAP] = sha(bootstrap)
    return raw_sources, hashes


def no_network(*args, **kwargs):
    raise Refused("OFFLINE_CHECK_NETWORK_FORBIDDEN")


@contextlib.contextmanager
def offline():
    with patch.object(socket.socket, "connect", no_network), patch.object(socket.socket, "connect_ex", no_network), \
         patch.object(socket, "create_connection", no_network), patch.object(socket, "getaddrinfo", no_network):
        yield


def bind_module(module, relative, raw_sources, root):
    path = Path(module.__file__)
    require(path.resolve() == (root / relative).resolve() and regular_read(path) == raw_sources[relative], "IMPORTED_SOURCE_CHANGED")


def assigned_value(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return node.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
    raise Refused("DECLARED_CONTRACT_MISSING")


def registry_check(raw, config):
    value = assigned_value(ast.parse(raw), "DATALAKE_TOOL_NAMES")
    require(isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords,
            "DATALAKE_REGISTRY_SHAPE")
    names = frozenset(ast.literal_eval(value.args[0]))
    require(names == DATALAKE_NAMES, "DATALAKE_REGISTRY_CHANGED")
    tools = getattr(config, "tools", None)
    require(isinstance(tools, list) and len(tools) <= 512, "TOOL_CONFIGURATION_SHAPE")
    registrations = [tool for tool in tools if getattr(tool, "name", None) in DATALAKE_NAMES | RETIRED_NAMES]
    require(len(registrations) == 2 and {tool.name for tool in registrations} == DATALAKE_NAMES, "DATALAKE_CONFIG_REGISTRATION_CHANGED")
    for tool in registrations:
        require(tool.use == "deerflow.community.appforge.tools:" + tool.name + "_tool", "DATALAKE_CONFIG_EXPORT_CHANGED")
    return sorted(names)


def progress_check(root, raw_sources):
    relative = "packages/harness/deerflow/community/appforge/data_qa_progress.py"
    path = root / relative
    # This pinned module imports only collections. Loading its real installed
    # source directly avoids importing the provider package's other tools.
    spec = importlib.util.spec_from_file_location("_oa_feature_installed_progress", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind_module(module, relative, raw_sources, root)
    events = []
    progress = module.DataQAProgress(SimpleNamespace(context={"thread_id": "synthetic-thread", "run_id": "synthetic-run"}, config={},
                                                    tool_call_id="synthetic-call", stream_writer=events.append))
    for stage in ("waiting", "waiting", "private synthetic content", "answering", "completed", "failed"):
        progress.emit(stage)
    require([event["stage"] for event in events] == ["waiting", "answering", "completed"], "PROGRESS_LIFECYCLE_CHANGED")
    require([event["sequence"] for event in events] == [1, 2, 3], "PROGRESS_SEQUENCE_CHANGED")
    require(all(set(event) == {"type", "thread_id", "run_id", "tool_call_id", "tool", "stage", "sequence"} and
                event["tool"] == "datalake_data_qa" and event["type"] == "data_qa_progress" for event in events), "PROGRESS_ENVELOPE_CHANGED")


def glm_check(raw_sources, root):
    prefix = "packages/harness/deerflow/models/"
    guard = root / (prefix + "glm_stream_guard.py")
    if not guard.exists():
        require(not any(prefix + name in raw_sources for name in GLM_FILES), "PINNED_GLM_GUARD_MISSING")
        return False
    require(all(prefix + name in raw_sources for name in GLM_FILES), "GLM_SOURCE_PINS_REQUIRED")
    enterprise = ast.parse(raw_sources[prefix + "enterprise_glm.py"])
    classes = [node for node in enterprise.body if isinstance(node, ast.ClassDef) and node.name == "EnterpriseGLMChat"]
    require(len(classes) == 1, "GLM_ADAPTER_MISSING")
    hooks = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == "_prepare_provider_stream"]
    require(len(hooks) == 1 and any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "install_glm_stream_guard"
                                 for node in ast.walk(hooks[0])), "GLM_STREAM_HOOK_MISSING")
    decoder = ast.parse(raw_sources[prefix + "glm_stream_guard.py"])
    require(any(isinstance(node, ast.ClassDef) and node.name == "GLMStreamDecoder" for node in decoder.body), "GLM_DECODER_MISSING")
    require(any(isinstance(node, ast.FunctionDef) and node.name == "install_glm_stream_guard" for node in decoder.body), "GLM_INSTALLER_MISSING")
    # Exact four-file hashes bind the previously qualified sync/async SDK seam.
    # Do not instantiate a provider model or repeat a provider canary per worker.
    return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise Refused("HEALTH_REDIRECT_FORBIDDEN")


def health():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open("http://127.0.0.1:8001/health", timeout=5) as response:
        require(response.status == 200 and response.geturl() == "http://127.0.0.1:8001/health", "LOCAL_HEALTH_STATUS")
        raw = response.read(16385)
        require(len(raw) <= 16384 and isinstance(json.loads(raw), dict), "LOCAL_HEALTH_RESPONSE")
    return 200


def runtime(args, root=ROOT):
    raw_sources, hashes = verify_sources(args["pin"], root)
    # -I excludes the application working directory; use the fixed installed root.
    sys.path.insert(0, str(root))
    with offline():
        from app.gateway import oa_policy_flow_config
        from deerflow.config import get_app_config
        from deerflow.persistence import bootstrap

        bind_module(oa_policy_flow_config, "app/gateway/oa_policy_flow_config.py", raw_sources, root)
        bind_module(bootstrap, BOOTSTRAP, raw_sources, root)
        require(bootstrap._get_head_revision() == REVISION, "INSTALLED_HEAD_CHANGED")
        actual = get_app_config()
        oa = oa_policy_flow_config.load_oa_policy_flow_config()
        require(oa.multi_result_enabled is False, "V2_OPT_IN_CHANGED")
        require(actual.scheduler.recursion_limit == 300 and actual.max_recursion_limit == 2000 and oa.recursion_limit == 1000, "SCOPED_BUDGET_CHANGED")
        names = registry_check(raw_sources[REGISTRY], actual)
        progress_check(root, raw_sources)
        glm_verified = glm_check(raw_sources, root)
    require(verify_sources(args["pin"], root)[1] == hashes, "INSTALLED_SOURCES_CHANGED_DURING_CHECK")
    return dict(status="PASS", sources_verified=True, source_hashes=hashes, migration_head=REVISION, v2_enabled=False, scheduler_budget=300,
                oa_budget=1000, global_budget=2000, datalake_tools=names, progress_contract_verified=True, glm_guard_verified=glm_verified,
                local_health_http_status=health(), database_access=False,
                checks=["installed_sources", "installed_0021_head", "v2_disabled", "scoped_budgets", "datalake_registry", "progress_contract", "preserved_glm_guard", "local_health"])


def database_fingerprint(url):
    parsed = urlsplit(url)
    return sha(json.dumps([parsed.hostname, parsed.port or 5432, parsed.path], sort_keys=True).encode())


async def database(args, root=ROOT):
    raw_sources, hashes = verify_sources(args["pin"], root)
    sys.path.insert(0, str(root))
    with offline():
        from deerflow.config import get_app_config
        config = get_app_config().database
    require(config.backend == "postgres", "POSTGRES_REQUIRED")
    fingerprint = database_fingerprint(config.postgres_url)
    require(fingerprint == args["expected_database_fingerprint"], "DATABASE_FINGERPRINT_MISMATCH")
    import asyncpg
    url = config.postgres_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(url, timeout=5, command_timeout=10, server_settings={"statement_timeout": "10000", "lock_timeout": "2000"})
    try:
        async with connection.transaction(readonly=True, isolation="repeatable_read"):
            revisions = [row[0] for row in await connection.fetch("SELECT version_num FROM alembic_version LIMIT 8")]
            require(revisions == [REVISION], "DATABASE_REVISION_CHANGED")
            rows = await connection.fetch("""SELECT column_name,data_type,character_maximum_length,is_nullable,column_default
                FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='oa_policy_flow_jobs'
                AND column_name IN ('result_protocol','result_files') ORDER BY column_name""")
            columns = {row["column_name"]: dict(row) for row in rows}
            protocol, files = columns.get("result_protocol", {}), columns.get("result_files", {})
            default = protocol.get("column_default")
            require(protocol.get("data_type") == "character varying" and protocol.get("character_maximum_length") == 8 and
                    protocol.get("is_nullable") == "NO" and isinstance(default, str) and
                    default.strip() in ("'v1'", "'v1'::character varying", "'v1'::varchar", "'v1'::text") and
                    files.get("data_type") == "json" and files.get("is_nullable") == "YES" and files.get("column_default") is None,
                    "OA_ADDITIVE_SCHEMA_SHAPE")
    finally:
        await connection.close(timeout=5)
    require(verify_sources(args["pin"], root)[1] == hashes, "INSTALLED_SOURCES_CHANGED_DURING_CHECK")
    return dict(status="PASS", sources_verified=True, source_hashes=hashes, database_readonly=True, database_access=True,
                database_fingerprint=fingerprint, revisions=revisions, additive_schema_verified=True)


def error_metadata(exc):
    if isinstance(exc, Refused):
        return {"error_code": exc.code, "error_category": "refused"}
    state = getattr(exc, "sqlstate", None)
    if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state):
        return {"error_code": "DATABASE_READ_FAILED", "error_category": "database_error", "sqlstate": state}
    return {"error_code": "FEATURE_CHECK_FAILED", "error_category": "timeout" if isinstance(exc, TimeoutError) else "runtime_error"}


def main():
    result = dict(status="INCOMPLETE", provider_requests=0, oa_callback_requests=0, configuration_modified=False, schema_migrated=False,
                  raw_errors_exported=False, business_values_exported=False)

    def deadline():
        try:
            os.write(1, (json.dumps(dict(result, status="INCOMPLETE", error_code="PROCESS_DEADLINE"), sort_keys=True) + "\n").encode())
        finally:
            os._exit(124)

    timer = threading.Timer(DEADLINE_SECONDS, deadline)
    timer.daemon = True
    timer.start()
    try:
        logging.disable(logging.CRITICAL)
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                require(len(sys.argv) == 2 and len(sys.argv[1]) <= 65536, "ARGUMENTS_INVALID")
                args = json.loads(sys.argv[1])
                if args.get("action") == "runtime":
                    result.update(runtime(args))
                elif args.get("action") == "database":
                    result.update(asyncio.run(database(args)))
                else:
                    raise Refused("ACTION_REFUSED")
            except Exception as exc:
                result.update(status="INCOMPLETE", **error_metadata(exc))
    finally:
        timer.cancel()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
