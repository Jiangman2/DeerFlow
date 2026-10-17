#!/usr/bin/env python3
"""Bounded, read-only follow-up for one exact post-update baseline.

Requires frozen refresh_baseline_v3.py beside this file. No application imports,
rollouts, migrations, image pulls, model/site requests, or shared-storage writes.
The PostgreSQL metadata probe is the sole intentional network client besides
kubectl. It opens only a read-only session/transaction to the configured DB.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys

HELPER_SHA = "ac6d909f579bddef02675eb248b662eba44c5b5eba1cdd65a24bf7889100a92b"
BASELINE_SHA = "d7e53f8fb380984a2055c30f5e17bc685867716f847720fa480a8d31a811ba99"
BASE = "/app/backend/"
RUNNER_FILES = tuple(BASE + p for p in (
    "app/gateway/deps.py",
    "packages/harness/deerflow/persistence/migrations/versions/0018_scheduled_task_reliability.py",
    "packages/harness/deerflow/persistence/migrations/versions/0019_digital_employee_agents.py"))
GATEWAY_FILES = tuple(BASE + "packages/harness/deerflow/runtime/runs/" + p for p in (
    "manager.py", "worker.py", "schemas.py", "stream_cleanup.py", "stream_publish_batch.py")) + (
    BASE + "app/gateway/routers/runs.py", BASE + "packages/harness/deerflow/models/output_safety.py")
CONFIG_CODE = {BASE + "packages/harness/deerflow/config/" + p: h for p, h in (
    ("app_config.py", "8a4bda9fc1000ea300ab267bd303efd284766e776eb2038923612d4f8cf32479"),
    ("database_config.py", "a3361513ee7c8c90099d61f23a324be356c1d4574ea9715a4afc158042762a50"),
    ("runtime_paths.py", "0e0b987090a187e8b2c87df01108e9b4bd5d54d7f7191a87acef47094a1c2fb3"))}
TABLES = ("ppt_confirmation_tasks", "ppt_confirmation_gates")
SHA = re.compile(r"[0-9a-f]{64}$")
GATEWAY_SHELL = "cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"
FRONTEND_SHELL = "cd /app/frontend && pnpm start"


class Failure(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Failure(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_read(path, limit, private=False):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "unsafe_input_path")
    for parent in (path.parent,) + tuple(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink(), "unsafe_input_ancestor")
        if private:
            require(info.st_uid == 0 and not info.st_mode & 0o022, "unsafe_input_permissions")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink() and before.st_size <= limit,
            "unsafe_input_file")
    if private:
        require(before.st_uid == 0 and not before.st_mode & 0o022, "unsafe_input_permissions")
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    def identity(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(len(raw) <= limit and identity(before) == identity(opened) == identity(after)
            == identity(path.lstat()), "input_changed")
    return raw


def helper(private=False):
    path = Path(__file__).resolve().with_name("refresh_baseline_v3.py")
    raw = safe_read(path, 100000, private)
    require(digest(raw) == HELPER_SHA, "helper_hash_mismatch")
    # Execute the exact checked bytes; never reopen the file for code loading.
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("frozen_v3", loader=None))
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def baseline_read(path, private=False):
    raw = safe_read(path, 16 * 1024 * 1024, private)
    require(digest(raw) == BASELINE_SHA, "baseline_hash_mismatch")
    value = json.loads(raw)
    require(value.get("schema") == "portal-ppt-readonly-baseline.v3"
            and value.get("collection") == "COMPLETE_REVIEW_REQUIRED" and value.get("namespace") == "deer-flow",
            "baseline_schema_mismatch")
    require(set(value["components"]) == {"gateway", "frontend", "ppt-master-runner", "provisioner"},
            "baseline_components_mismatch")
    for row in value["components"].values():
        require(row["stable"] is True and row["before"] == row["after"]
                and row["pods_before"] == row["pods_after"], "baseline_unstable")
    return value


# This string is sent only to the already-bound representative Gateway Pod.
# All query text is constant. It never imports deerflow/app or creates a pool.
DB_PROBE = r'''
import asyncio, hashlib, json, os, re, signal, stat
from pathlib import Path
signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
signal.alarm(35)
CODE = __CODE__
DEFAULT_CONFIG_AUTHORIZED = __DEFAULT_CONFIG_AUTHORIZED__
CONFIG_PATH_RESOLUTION = None
TABLES = ("ppt_confirmation_tasks", "ppt_confirmation_gates")
class Closed(Exception): pass
def check(ok, code):
    if not ok: raise Closed(code)
def read_file(name, limit):
    p=Path(name)
    before=p.lstat()
    check(stat.S_ISREG(before.st_mode) and str(p.resolve())==name and before.st_size<=limit,"config_file_unsafe")
    fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,"rb") as stream:
        opened=os.fstat(stream.fileno()); raw=stream.read(limit+1); after=os.fstat(stream.fileno())
    def identity(s): return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    check(len(raw)<=limit and identity(before)==identity(opened)==identity(after)==identity(p.lstat()),"config_file_changed")
    return raw
def configured_url():
    global CONFIG_PATH_RESOLUTION
    check(not os.environ.get("UV_ENV_FILE"), "uv_env_file_not_supported")
    check(not any(os.environ.get(k) for k in ("UV_WORKING_DIR","UV_PROJECT","UV_CONFIG_FILE")), "uv_runtime_override_not_supported")
    for name, expected in CODE.items():
        check(hashlib.sha256(read_file(name,1048576)).hexdigest()==expected,"config_resolver_code_changed")
    explicit=os.environ.get("DEER_FLOW_CONFIG_PATH")
    if explicit:
        check(explicit=="/app/backend/config.yaml", "nonstandard_config_path")
        CONFIG_PATH_RESOLUTION="explicit_fixed_config_path"
    else:
        # Controller only sends this probe after exact cd-backend argv validation.
        # /app is the initial exec cwd, and the known launcher cd yields backend.
        check(DEFAULT_CONFIG_AUTHORIZED or os.getcwd()=="/app", "default_config_path_not_proven")
        check(os.environ.get("DEER_FLOW_PROJECT_ROOT") in (None,"","/app/backend"), "nonstandard_project_root")
        CONFIG_PATH_RESOLUTION="known_launcher_metadata" if DEFAULT_CONFIG_AUTHORIZED else "known_launcher_runtime_cwd"
    # load_dotenv() changes the application process environment, not kubectl exec's.
    # Refuse that ambiguity instead of reading or exporting dotenv credentials.
    dotenv_paths=set(str(p/".env") for name in CODE for p in Path(name).parents)
    check(not any(os.path.lexists(p) for p in dotenv_paths), "dotenv_resolution_not_supported")
    import yaml
    config=yaml.safe_load(read_file("/app/backend/config.yaml",2*1024*1024))
    check(isinstance(config,dict) and isinstance(config.get("database"),dict),"database_config_missing")
    database=config["database"]
    def resolve(value):
        check(isinstance(value,str),"database_config_invalid")
        if value.startswith("$"):
            check(re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]{0,127}",value),"database_env_reference_invalid")
            value=os.environ.get(value[1:])
        check(isinstance(value,str) and 0<len(value)<=16384,"database_config_invalid")
        return value
    check(resolve(database.get("backend","sqlite"))=="postgres","database_not_postgres")
    url=resolve(database.get("postgres_url"))
    if url.startswith("postgresql+asyncpg://"): url="postgresql://"+url[len("postgresql+asyncpg://"):]
    check(url.startswith(("postgresql://","postgres://")),"unsupported_database_scheme")
    return url
async def query(url):
    import asyncpg
    conn=None
    try:
        conn=await asyncpg.connect(dsn=url,timeout=5,command_timeout=3,server_settings={
            "default_transaction_read_only":"on","statement_timeout":"3000","lock_timeout":"1000",
            "idle_in_transaction_session_timeout":"5000","application_name":"portal_ppt_readonly_metadata"})
        async with conn.transaction(isolation="repeatable_read",readonly=True):
            row=await conn.fetchrow("SELECT current_setting('transaction_read_only') AS transaction_read_only, current_setting('default_transaction_read_only') AS session_read_only, pg_catalog.to_regclass('alembic_version') IS NOT NULL AS alembic_exists, pg_catalog.to_regclass('ppt_confirmation_tasks') IS NOT NULL AS tasks_exists, pg_catalog.to_regclass('ppt_confirmation_gates') IS NOT NULL AS gates_exists")
            check(row["transaction_read_only"]=="on" and row["session_read_only"]=="on","database_not_readonly")
            versions=[]
            if row["alembic_exists"]:
                versions=[r["version_num"] for r in await conn.fetch("SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 17")]
                check(len(versions)<=16 and all(isinstance(v,str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",v) for v in versions),"migration_versions_invalid")
            return {"status":"COMPLETE","session_read_only":True,"transaction_read_only":True,
                "alembic_table_exists":bool(row["alembic_exists"]),"alembic_versions":versions,
                "tables":{"ppt_confirmation_tasks":bool(row["tasks_exists"]),"ppt_confirmation_gates":bool(row["gates_exists"])},
                "config_resolution_code_verified":True,"config_path_resolution":CONFIG_PATH_RESOLUTION,
                "scope":"configured_database_current_search_path"}
    finally:
        if conn is not None:
            try: await conn.close(timeout=2)
            except Exception: conn.terminate()
try:
    result=asyncio.run(asyncio.wait_for(query(configured_url()),timeout=18))
except Closed as exc:
    result={"status":"NOT_COLLECTED","code":exc.args[0]}
except ImportError:
    result={"status":"NOT_COLLECTED","code":"metadata_dependency_unavailable"}
except Exception:
    result={"status":"NOT_COLLECTED","code":"database_metadata_failed"}
print(json.dumps(result,sort_keys=True))
'''
DB_CODES = {"config_file_unsafe", "config_file_changed", "config_resolver_code_changed", "nonstandard_config_path",
            "nonstandard_project_root", "default_config_path_not_proven", "dotenv_resolution_not_supported", "database_config_missing",
            "database_config_invalid", "database_env_reference_invalid", "database_not_postgres",
            "unsupported_database_scheme", "database_not_readonly", "migration_versions_invalid",
            "metadata_dependency_unavailable", "database_metadata_failed", "application_environment_not_proven",
            "uv_env_file_not_supported", "uv_runtime_override_not_supported"}


def db_source(default_authorized=False):
    require(type(default_authorized) is bool, "invalid_config_authority")
    return DB_PROBE.replace("__CODE__", repr(CONFIG_CODE)).replace(
        "__DEFAULT_CONFIG_AUTHORIZED__", repr(default_authorized)).encode("utf-8")


def clean_db(value):
    require(isinstance(value, dict), "invalid_db_receipt")
    if value.get("status") == "NOT_COLLECTED":
        require(set(value) == {"status", "code"} and value["code"] in DB_CODES, "invalid_db_error")
        return dict(value)
    require(set(value) == {"status", "session_read_only", "transaction_read_only", "alembic_table_exists",
                          "alembic_versions", "tables", "config_resolution_code_verified", "config_path_resolution", "scope"}, "invalid_db_fields")
    require(value["status"] == "COMPLETE" and value["session_read_only"] is True
            and value["transaction_read_only"] is True and value["config_resolution_code_verified"] is True
            and value["scope"] == "configured_database_current_search_path", "invalid_db_readonly_receipt")
    require(value["config_path_resolution"] in ("explicit_fixed_config_path", "known_launcher_metadata",
            "known_launcher_runtime_cwd"), "invalid_config_resolution_receipt")
    require(type(value["alembic_table_exists"]) is bool and isinstance(value["tables"], dict)
            and set(value["tables"]) == set(TABLES) and all(type(v) is bool for v in value["tables"].values()),
            "invalid_db_tables")
    versions = value["alembic_versions"]
    require(isinstance(versions, list) and len(versions) <= 16 and all(isinstance(v, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in versions)
            and (value["alembic_table_exists"] or not versions), "invalid_db_versions")
    return value


def source_script(h, component):
    paths = RUNNER_FILES if component == "ppt-master-runner" else GATEWAY_FILES
    return h.PYTHON_PROBE.replace("__FILES__", json.dumps(paths)).replace("__PACKAGES__", "[]").encode("utf-8")


def clean_sources(h, value, component):
    paths = RUNNER_FILES if component == "ppt-master-runner" else GATEWAY_FILES
    require(set(value) == {"sources", "dependencies", "runtime", "architecture"}
            and value["dependencies"] == {} and set(value["sources"]) == set(paths), "invalid_source_receipt")
    require(isinstance(value["runtime"], str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", value["runtime"])
            and value["architecture"] in ("x86_64", "amd64", "arm64", "aarch64", "OTHER"), "invalid_source_runtime")
    for row in value["sources"].values():
        require(isinstance(row, dict) and row.get("status") in h.SOURCE_STATUSES, "invalid_source_status")
        if row["status"] == "PRESENT":
            require(set(row) == {"status", "sha256", "bytes"} and isinstance(row["sha256"], str)
                    and SHA.fullmatch(row["sha256"]) and type(row["bytes"]) is int
                    and 0 <= row["bytes"] <= 4194304, "invalid_source_hash")
        else:
            require(set(row) == {"status"}, "invalid_source_error")
    return value


def inventory(h, kube, baseline):
    safe, raw = {}, {}
    for component in h.COMPONENTS:
        deployment = h.deployment_get(kube, component)
        pods = h.owned_pods(kube, deployment)
        fp = h.fingerprint(deployment)
        identities = sorted((h.pod_identity(p, component) for p in pods), key=lambda p: p["name"])
        expected = baseline["components"][component]
        require(h.converged(fp) and fp == expected["after"]
                and identities == expected["pods_after"], "baseline_identity_drift")
        active = [p for p in identities if p["classification"] != "CONTROL_PLANE_TERMINAL"]
        require(len(active) == fp["desired"] and all(p["ready"] for p in active), "baseline_not_ready")
        safe[component] = {"deployment": fp, "pods": identities, "ready": len(active),
                           "terminal": len(identities) - len(active)}
        raw[component] = deployment
    return safe, raw


def representative(snapshot, component):
    rows = [p for p in snapshot[component]["pods"] if p["ready"]]
    require(bool(rows), "no_ready_representative")
    return rows[0]


def exec_probe(h, pod, component, kind, steps, default_authorized=False, application_env_authorized=False):
    require(component in ("gateway", "ppt-master-runner") and pod["ready"], "unready_probe_forbidden")
    require(kind in ("source", "database") and (kind != "database" or component == "gateway"), "unapproved_probe")
    if kind == "database" and application_env_authorized is not True:
        steps.append({"operation": "gateway_database", "status": "NOT_COLLECTED", "code": "application_environment_not_proven"})
        return {"status": "NOT_COLLECTED", "code": "application_environment_not_proven"}
    name = h.identifier(pod["name"])
    source = db_source(default_authorized) if kind == "database" else source_script(h, component)
    args = ["kubectl", "--request-timeout=30s", "-n", "deer-flow", "exec", "-i", name, "-c", component,
            "--", "/app/backend/.venv/bin/python", "-I", "-B", "-"]
    step = {"operation": component + "_" + kind, "status": "STARTED"}
    steps.append(step)
    try:
        raw, evidence = h.run_bounded(args, source, timeout=60, limit=1024 * 1024)
        step.update(evidence)
        value = json.loads(raw)
        clean = clean_db(value) if kind == "database" else clean_sources(h, value, component)
        step["status"] = "COMPLETE"
        return clean
    except Exception as exc:
        step.update(status="FAILED", code="probe_failed")
        if isinstance(exc, h.CheckFailure):
            step.update(exc.evidence)
        return {"status": "NOT_COLLECTED", "code": "probe_failed"}


def launcher(command, args, working_directory):
    combined = command + args
    frontend = combined
    if frontend and frontend[0] in ("docker-entrypoint.sh", "/usr/local/bin/docker-entrypoint.sh"):
        frontend = frontend[1:]
    if frontend in (["node", "server.js"], ["/usr/local/bin/node", "server.js"]):
        return "NODE_STANDALONE"
    if frontend in (["pnpm", "start"], ["pnpm", "run", "start"], ["sh", "-c", FRONTEND_SHELL]):
        return "PNPM_START"
    if working_directory == "/app" and combined in (
            ["sh", "-c", GATEWAY_SHELL],
            ["sh", "-c", GATEWAY_SHELL + " --workers 1"]):
        return "GATEWAY_BACKEND_CONFIG_DEFAULT"
    return "CUSTOM_REDACTED" if combined else "UNAVAILABLE"


def default_config_authority(h, deployment, image):
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
    command, args = container.get("command", []), container.get("args", [])
    if not command and not args:
        return image.get("status") == "COMPLETE" and image.get("default_launcher") == "GATEWAY_BACKEND_CONFIG_DEFAULT" \
            and container.get("workingDir", image.get("working_directory")) == "/app"
    # Partial Kubernetes command/arg overrides require image argv reconstruction,
    # deliberately unsupported. No arbitrary shell parsing is used here.
    if not command or not args:
        return False
    working = container.get("workingDir", image.get("working_directory"))
    return launcher(command, args, working) == "GATEWAY_BACKEND_CONFIG_DEFAULT"


def application_env_authority(deployment, image):
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")
    command, args = container.get("command", []), container.get("args", [])
    if not command and not args:
        return image.get("status") == "COMPLETE" and image.get("default_launcher") == "GATEWAY_BACKEND_CONFIG_DEFAULT"
    if not command or not args:
        return False
    # Exact known argv changes PYTHONPATH only, never database/config environment.
    # The synthetic cwd here classifies argv; default path requires separate proof.
    return launcher(command, args, "/app") == "GATEWAY_BACKEND_CONFIG_DEFAULT"


def image_metadata(h, data, tag, runtime_digests):
    require(isinstance(data, dict) and isinstance(data.get("status"), dict), "invalid_image_metadata")
    status = data["status"]
    image_id = h.image_digest(status.get("id"))
    require(image_id != "UNRECOGNIZED", "invalid_image_config_digest")
    tags = status.get("repoTags", [])
    require(isinstance(tags, list) and tag in tags, "image_tag_not_local")
    digests = status.get("repoDigests", [])
    require(isinstance(digests, list) and len(digests) <= 256, "invalid_image_repo_digests")
    manifests = sorted({h.image_digest(d) for d in digests if h.image_ref(d) == d
                        and "@sha256:" in d and h.image_digest(d) != "UNRECOGNIZED"})
    require(set(runtime_digests) <= set(manifests + [image_id]), "runtime_image_not_bound")
    info = data.get("info", {})
    if isinstance(info, str):
        info = json.loads(info)
    require(isinstance(info, dict), "invalid_image_info")
    spec = info.get("imageSpec", {})
    if isinstance(spec, str):
        spec = json.loads(spec)
    require(isinstance(spec, dict), "invalid_image_spec")
    config = spec.get("config", {})
    require(isinstance(config, dict), "invalid_image_config")
    architecture = spec.get("architecture")
    architecture = architecture if architecture in ("amd64", "arm64", "x86_64", "aarch64") else "UNAVAILABLE"
    command, args = config.get("Entrypoint") or [], config.get("Cmd") or []
    require(isinstance(command, list) and isinstance(args, list) and len(command) + len(args) <= 256
            and all(isinstance(v, str) and len(v) <= 16384 for v in command + args), "invalid_image_launcher")
    working = h.safe_path(config.get("WorkingDir"))
    classification = launcher(command, args, working)
    return {"status": "COMPLETE", "tag": tag, "config_digest": image_id,
            "manifest_digests": manifests, "runtime_digests": sorted(set(runtime_digests)),
            "architecture": architecture, "os": "linux" if spec.get("os") == "linux" else "UNAVAILABLE",
            "default_launcher": classification, "working_directory": working,
            "command_args_sha256": h.digest(h.canonical([command, args])),
            "environment_values": "NOT_EXPORTED", "raw_metadata": "NOT_PERSISTED"}


def collect_images(h, before, steps):
    result = {}
    executable = shutil.which("crictl")
    for component in h.COMPONENTS:
        if not executable:
            result[component] = {"status": "NOT_COLLECTED", "code": "crictl_unavailable"}
            continue
        tag = before[component]["deployment"]["images"][component]
        require(h.image_ref(tag) == tag, "invalid_image_reference")
        ids = [p["image_digest"] for p in before[component]["pods"] if p["ready"]]
        step = {"operation": "inspect_local_image_" + component, "status": "STARTED"}
        steps.append(step)
        try:
            raw, evidence = h.run_bounded([executable, "inspecti", "--output=json", tag], None, timeout=20, limit=4 * 1024 * 1024)
            step.update(evidence)
            result[component] = image_metadata(h, json.loads(raw), tag, ids)
            step["status"] = "COMPLETE"
        except Exception:
            step.update(status="FAILED", code="local_image_metadata_unavailable")
            result[component] = {"status": "NOT_COLLECTED", "code": "local_image_metadata_unavailable"}
    return result


def provisioner_references(h, deployment):
    container = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "provisioner")
    result = {}
    for name in ("SANDBOX_IMAGE", "PPT_MASTER_AGENT_HOST_IMAGE"):
        rows = [(i, e) for i, e in enumerate(container.get("env", [])) if e.get("name") == name]
        if len(rows) != 1:
            result[name] = {"status": "NOT_COLLECTED", "code": "explicit_env_not_unique_or_absent"}
            continue
        index, entry = rows[0]
        row = {"position": "spec.template.spec.containers[provisioner].env[%d]" % index}
        if "value" in entry and "valueFrom" not in entry:
            value = h.image_ref(entry["value"])
            row.update(status="COMPLETE" if value == entry["value"] else "NOT_COLLECTED", image=value)
        elif "valueFrom" in entry and "value" not in entry:
            ref = entry["valueFrom"]
            kinds = [k for k in ("secretKeyRef", "configMapKeyRef", "fieldRef", "resourceFieldRef") if k in ref]
            row.update(status="NOT_COLLECTED", reference_type=kinds[0] if len(kinds) == 1 else "OTHER",
                       reference_sha256=h.digest(h.canonical(ref)), code="reference_not_dereferenced")
        else:
            row.update(status="NOT_COLLECTED", code="invalid_explicit_env")
        result[name] = row
    return result


def collect(h, baseline, kube=None, progress=None):
    kube = kube or h.Kubectl()
    progress = progress or (lambda _: None)
    report = {"schema": "portal-ppt-targeted-followup.v1", "baseline_sha256": BASELINE_SHA,
              "helper_sha256": HELPER_SHA, "collection": "INCOMPLETE", "steps": kube.steps,
              "source_scope": "one_bound_ready_pod_per_component_fixed_files_only",
              "database_scope": "configured_database_metadata_only_no_initialization",
              "environment_values": "NOT_EXPORTED", "production_mutation": False,
              "ready_for_build": False, "ready_for_rollout": False, "errors": [],
              "not_collected": ["agent_host_running_source", "sandbox_running_source", "shared_storage_validation",
                                "nginx_configuration", "database_business_rows", "full_image_manifest_bytes",
                                "fleet_wide_new_source_hashes"]}
    before = None
    try:
        progress("Checking all four service identities against the exact baseline")
        before, raw = inventory(h, kube, baseline)
        report["before"] = before
        report["provisioner_image_references"] = provisioner_references(h, raw["provisioner"])
        progress("Inspecting local image metadata without pulling images")
        report["images"] = collect_images(h, before, kube.steps)
        config_authority = default_config_authority(h, raw["gateway"], report["images"].get("gateway", {}))
        env_authority = application_env_authority(raw["gateway"], report["images"].get("gateway", {}))
        report["gateway_default_config_path_authorized"] = config_authority
        report["gateway_application_environment_authorized"] = env_authority
        report["frontend_deployed_launcher"] = h.runtime_shape(raw["frontend"], "frontend")
        report["representatives"] = {c: representative(before, c) for c in ("gateway", "ppt-master-runner")}
        for component in ("ppt-master-runner", "gateway"):
            inventory(h, kube, baseline)  # Stop before every exec if any identity changed.
            progress("Reading fixed source hashes: " + component)
            report[component + "_source"] = exec_probe(h, report["representatives"][component], component, "source", kube.steps)
        inventory(h, kube, baseline)
        progress("Reading PostgreSQL migration metadata in a read-only transaction")
        report["database"] = exec_probe(h, report["representatives"]["gateway"], "gateway", "database", kube.steps,
                                        default_authorized=config_authority, application_env_authorized=env_authority)
    except Exception as exc:
        code = exc.args[0] if isinstance(exc, Failure) else "collection_failed"
        report["errors"].append({"code": code})
    finally:
        if before is not None:
            try:
                progress("Rechecking final service identities")
                after, _ = inventory(h, kube, baseline)
                report["after"] = after
                report["identity_stable"] = after == before
            except Exception:
                report["identity_stable"] = False
                report["errors"].append({"code": "final_identity_check_failed"})
        else:
            report["identity_stable"] = False
    mandatory = {"database": report.get("database", {}).get("status") == "COMPLETE"}
    for component in ("gateway", "ppt-master-runner"):
        source = report.get(component + "_source", {}).get("sources", {})
        paths = GATEWAY_FILES if component == "gateway" else RUNNER_FILES
        mandatory[component + "_source"] = set(source) == set(paths) and all(
            row.get("status") == "PRESENT" for row in source.values())
    report["mandatory_evidence_complete"] = mandatory
    if not all(mandatory.values()):
        report["errors"].append({"code": "mandatory_evidence_incomplete"})
    if report["identity_stable"] and not report["errors"]:
        report["collection"] = "COMPLETE_WITH_DECLARED_GAPS"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    try:
        require(args.collect, "explicit_collect_required")
        require(os.name == "posix" and os.geteuid() == 0 and socket.gethostname().split(".")[0] == "datax-01",
                "root_on_datax01_required")
        h = helper(private=True)
        output = h.private_output(args.out)
        try:
            baseline = baseline_read(args.baseline, private=True)
            report = collect(h, baseline, progress=lambda text: print(text, flush=True))
        except Exception:
            report = {"schema": "portal-ppt-targeted-followup.v1", "collection": "INCOMPLETE",
                      "ready_for_build": False, "ready_for_rollout": False,
                      "errors": [{"code": "baseline_validation_failed"}]}
        report["utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        path, sha = h.write_report(output, report)
        print(json.dumps({"collection": report["collection"], "report": path, "sha256": sha,
                          "database": report.get("database", {"status": "NOT_COLLECTED"}),
                          "identity_stable": report.get("identity_stable", False)}, sort_keys=True))
        return 0 if report["collection"] == "COMPLETE_WITH_DECLARED_GAPS" else 1
    except Exception:
        print('{"collection":"INCOMPLETE","code":"safe_setup_failed","ready_for_build":false,"ready_for_rollout":false}')
        return 1


if __name__ == "__main__":
    sys.exit(main())
