"""Offline only: fake Kubernetes/driver/image metadata; no sockets or production."""
import ast
import asyncio
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("targeted_collector", ROOT / "collector.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
h = m.helper()
SECRET = "NEVER_EXPORT_CREDENTIAL_987654"


class Kube:
    def __init__(self):
        self.steps = []
        self.calls = []
        self.deployments, self.sets, self.pods = {}, {}, {}
        for c in h.COMPONENTS:
            n = 70 if c == "gateway" else 1
            self.deployments[c] = {"metadata": {"name": c, "uid": "deployment-" + c, "generation": 1},
                "spec": {"replicas": n, "selector": {"matchLabels": {"app": c}}, "template": {"spec": {
                    "containers": [{"name": c, "image": "local/" + c + ":fixed",
                    "env": [{"name": "API_KEY", "value": SECRET}], "args": [SECRET]}]}}},
                "status": {"observedGeneration": 1, "replicas": n, "updatedReplicas": n, "readyReplicas": n, "availableReplicas": n}}
            self.sets[c] = [{"metadata": {"uid": "rs-" + c, "ownerReferences": [{"kind": "Deployment", "uid": "deployment-" + c, "controller": True}]}}]
            self.pods[c] = [{"metadata": {"name": c + "-" + str(i), "uid": c + "-uid-" + str(i), "ownerReferences": [
                {"kind": "ReplicaSet", "name": "rs-" + c, "uid": "rs-" + c, "controller": True}]},
                "spec": {"nodeName": "node-1"}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{"name": c, "ready": True, "restartCount": 0, "state": {"running": {}},
                    "containerID": "containerd://" + c + str(i), "imageID": "containerd://sha256:" + "a" * 64}]}} for i in range(n)]
        self.deployments["gateway"]["spec"]["template"]["spec"]["containers"][0].update(
            command=["sh","-c"], args=[m.GATEWAY_SHELL])
    def call(self, args, stdin=None):
        h.validate_command(args, stdin)
        self.calls.append(args)
        if args[1] == "deployment":
            return copy.deepcopy(self.deployments[args[2]])
        selector = args[args.index("--selector") + 1]
        c = selector.split("=", 1)[1]
        return {"items": copy.deepcopy((self.sets if args[1] == "replicasets" else self.pods)[c])}
    def baseline(self):
        return {"schema": "portal-ppt-readonly-baseline.v3", "collection": "COMPLETE_REVIEW_REQUIRED", "namespace": "deer-flow",
            "components": {c: {"stable": True, "before": h.fingerprint(self.deployments[c]), "after": h.fingerprint(self.deployments[c]),
                "pods_before": sorted([h.pod_identity(p, c) for p in self.pods[c]], key=lambda p:p["name"]),
                "pods_after": sorted([h.pod_identity(p, c) for p in self.pods[c]], key=lambda p:p["name"])} for c in h.COMPONENTS}}


def source_receipt(c):
    paths = m.RUNNER_FILES if c == "ppt-master-runner" else m.GATEWAY_FILES
    return {"sources": {p: {"status": "PRESENT", "sha256": "b" * 64, "bytes": 10} for p in paths},
            "dependencies": {}, "architecture": "arm64", "runtime": "3.12.3"}


class Tests(unittest.TestCase):
    def test_frozen_helper_and_actual_baseline(self):
        self.assertEqual(m.digest((ROOT / "refresh_baseline_v3.py").read_bytes()), m.HELPER_SHA)
        path = os.environ.get("TARGET_BASELINE_FIXTURE", "C:/Users/15285/Downloads/202610171340/verified/baseline/REPORT.json")
        self.assertTrue(Path(path).is_file(), "Supply the exact captured baseline fixture")
        baseline = m.baseline_read(path)
        self.assertEqual(baseline["components"]["gateway"]["ready_count"], 70)
        self.assertEqual(baseline["collection"], "COMPLETE_REVIEW_REQUIRED")
        # Exact home/hotfix captured manifest argv, not a guessed live shell.
        actual_argv = [["sh","-c"],[m.GATEWAY_SHELL+" --workers 1"]]
        self.assertEqual(h.digest(h.canonical(actual_argv)),
                         baseline["components"]["gateway"]["runtime_shape"]["command_args_sha256"])
        self.assertEqual(m.launcher(*actual_argv, "/app"),"GATEWAY_BACKEND_CONFIG_DEFAULT")

    def test_tampered_baseline_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "REPORT.json"
            p.write_text('{"token":"' + SECRET + '"}')
            with self.assertRaisesRegex(m.Failure, "baseline_hash_mismatch"):
                m.baseline_read(p)

    def test_all_seventy_identities_but_only_three_execs(self):
        kube = Kube()
        seen = []
        def run(args, stdin, **kwargs):
            seen.append((args, stdin))
            c = args[args.index("-c") + 1]
            value = {"status": "NOT_COLLECTED", "code": "database_not_postgres"} if stdin == m.db_source() else source_receipt(c)
            return json.dumps(value).encode(), {"returncode": 0}
        with mock.patch.object(h, "run_bounded", side_effect=run), mock.patch.object(m.shutil, "which", return_value=None):
            report = m.collect(h, kube.baseline(), kube)
        self.assertTrue(report["identity_stable"])
        self.assertEqual(len(report["before"]["gateway"]["pods"]), 70)
        self.assertEqual(len(seen), 3)
        self.assertEqual(sum(a[a.index("-c")+1] == "gateway" for a, _ in seen), 2)
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertTrue(all(a[:5] == ["kubectl", "--request-timeout=30s", "-n", "deer-flow", "exec"] for a, _ in seen))
        self.assertTrue(all(a[-4:] == ["/app/backend/.venv/bin/python", "-I", "-B", "-"] for a, _ in seen))
        self.assertEqual(report["images"]["gateway"]["code"], "crictl_unavailable")
        self.assertEqual(report["collection"], "INCOMPLETE")
        self.assertFalse(report["mandatory_evidence_complete"]["database"])

    def test_optional_cri_gap_does_not_mask_mandatory_source_gap(self):
        kube = Kube()
        database = {"status":"COMPLETE", "session_read_only":True, "transaction_read_only":True,
                    "alembic_table_exists":False, "alembic_versions":[],
                    "tables":dict.fromkeys(m.TABLES,False), "config_resolution_code_verified":True,
                    "config_path_resolution":"explicit_fixed_config_path",
                    "scope":"configured_database_current_search_path"}
        missing = False
        def run(args, stdin, **kwargs):
            c = args[args.index("-c") + 1]
            result = database if stdin == m.db_source() else source_receipt(c)
            if missing and "sources" in result:
                result["sources"][next(iter(result["sources"]))] = {"status":"MISSING"}
            return json.dumps(result).encode(), {}
        with mock.patch.object(h,"run_bounded",side_effect=run), mock.patch.object(m.shutil,"which",return_value=None):
            report = m.collect(h,kube.baseline(),kube)
            self.assertEqual(report["collection"],"COMPLETE_WITH_DECLARED_GAPS")
            self.assertFalse(report["ready_for_build"])
            self.assertFalse(report["ready_for_rollout"])
            missing = True
            report = m.collect(h,kube.baseline(),kube)
            self.assertEqual(report["collection"],"INCOMPLETE")

    def test_unowned_pod_ignored_and_owner_drift_rejected(self):
        kube = Kube()
        baseline = kube.baseline()
        other = copy.deepcopy(kube.pods["gateway"][0])
        other["metadata"]["ownerReferences"][0]["uid"] = "unrelated-rs"
        kube.pods["gateway"].append(other)
        before, _ = m.inventory(h, kube, baseline)
        self.assertEqual(len(before["gateway"]["pods"]), 70)
        kube.sets["gateway"][0]["metadata"]["ownerReferences"][0]["uid"] = "different-deployment"
        with self.assertRaisesRegex(m.Failure, "baseline_identity_drift"):
            m.inventory(h, kube, baseline)

    def test_changed_or_unready_pod_prevents_all_exec(self):
        for key in ("uid", "ready", "imageID", "containerID"):
            kube = Kube()
            baseline = kube.baseline()
            target = kube.pods["gateway"][0]
            if key == "uid": target["metadata"][key] = "new-uid"
            else: target["status"]["containerStatuses"][0][key] = False if key == "ready" else "changed"
            with mock.patch.object(h, "run_bounded") as run:
                report = m.collect(h, baseline, kube)
            run.assert_not_called()
            self.assertEqual(report["collection"], "INCOMPLETE")

    def test_midway_drift_preserves_receipt_and_stops_db(self):
        kube = Kube()
        baseline = kube.baseline()
        def run(args, stdin, **kwargs):
            kube.pods["gateway"][0]["metadata"]["uid"] += "-changed"
            return json.dumps(source_receipt("ppt-master-runner")).encode(), {}
        with mock.patch.object(h, "run_bounded", side_effect=run) as runner:
            report = m.collect(h, baseline, kube)
        self.assertEqual(runner.call_count, 1)
        self.assertIn("ppt-master-runner_source", report)
        self.assertNotIn("database", report)
        self.assertFalse(report["identity_stable"])

    def test_failure_is_redacted_and_other_steps_retained(self):
        kube = Kube()
        with mock.patch.object(h, "run_bounded", side_effect=RuntimeError(SECRET)), mock.patch.object(m.shutil, "which", return_value=None):
            report = m.collect(h, kube.baseline(), kube)
        self.assertEqual(len(report["steps"]), 3)
        self.assertEqual(report["database"]["status"], "NOT_COLLECTED")
        self.assertNotIn(SECRET, json.dumps(report))

    def test_source_schema_strict_values(self):
        for field in ("sha256", "bytes", "status"):
            value = source_receipt("gateway")
            value["sources"][m.GATEWAY_FILES[0]][field] = SECRET
            with self.assertRaises(m.Failure): m.clean_sources(h, value, "gateway")
        value = source_receipt("gateway")
        value["sources"][m.GATEWAY_FILES[0]]["extra"] = SECRET
        with self.assertRaises(m.Failure): m.clean_sources(h, value, "gateway")

    def test_db_receipt_rejects_extras_and_unsafe_versions(self):
        for value in ({"status":"NOT_COLLECTED", "code":SECRET},
                      {"status":"NOT_COLLECTED", "code":"database_metadata_failed", "dsn":SECRET}):
            with self.assertRaises(m.Failure): m.clean_db(value)

    def test_exact_probe_allowlist_and_unready(self):
        pod = {"name":"gateway-1", "ready":False}
        with mock.patch.object(h, "run_bounded") as run:
            with self.assertRaises(m.Failure): m.exec_probe(h, pod, "gateway", "database", [])
            pod["ready"] = True
            with self.assertRaises(m.Failure): m.exec_probe(h, pod, "gateway", "shell", [])
            with self.assertRaises(m.Failure): m.exec_probe(h, pod, "ppt-master-runner", "database", [])
        run.assert_not_called()

    def test_image_projection_binds_config_and_manifest_without_secrets(self):
        value = {"status":{"id":"sha256:" + "a"*64, "repoTags":["local/frontend:fixed"],
                           "repoDigests":["local/frontend@sha256:" + "b"*64]},
                 "info":{"imageSpec":{"architecture":"arm64", "os":"linux", "config":{
                     "Env":["TOKEN="+SECRET], "Cmd":["node","server.js", "--token="+SECRET]}}}}
        result = m.image_metadata(h, value, "local/frontend:fixed", ["sha256:"+"a"*64])
        self.assertEqual(result["manifest_digests"], ["sha256:"+"b"*64])
        self.assertEqual(result["architecture"], "arm64")
        self.assertEqual(result["default_launcher"], "CUSTOM_REDACTED")
        self.assertNotIn(SECRET, json.dumps(result))
        with self.assertRaisesRegex(m.Failure, "runtime_image_not_bound"):
            m.image_metadata(h, value, "local/frontend:fixed", ["sha256:"+"c"*64])

    def test_crictl_only_inspects_fixed_baseline_tags(self):
        kube = Kube()
        before, _ = m.inventory(h, kube, kube.baseline())
        calls = []
        def run(args, stdin, **kwargs):
            calls.append(args)
            raise RuntimeError(SECRET)
        with mock.patch.object(m.shutil, "which", return_value="/usr/bin/crictl"), mock.patch.object(h, "run_bounded", side_effect=run):
            result = m.collect_images(h, before, [])
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(c[:3] == ["/usr/bin/crictl", "inspecti", "--output=json"] and len(c) == 4 for c in calls))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_provisioner_literal_and_secret_reference_projection(self):
        deployment = Kube().deployments["provisioner"]
        deployment["spec"]["template"]["spec"]["containers"][0]["env"] = [
            {"name":"SANDBOX_IMAGE","value":"local/sandbox:fixed"},
            {"name":"PPT_MASTER_AGENT_HOST_IMAGE","valueFrom":{"secretKeyRef":{"name":SECRET,"key":SECRET}}}]
        value = m.provisioner_references(h, deployment)
        self.assertEqual(value["SANDBOX_IMAGE"]["image"], "local/sandbox:fixed")
        self.assertEqual(value["PPT_MASTER_AGENT_HOST_IMAGE"]["reference_type"], "secretKeyRef")
        self.assertNotIn(SECRET, json.dumps(value))

    def db_namespace(self):
        tree = ast.parse(m.db_source().decode())
        # Load the exact functions while suppressing only main/probe alarm in the test.
        tree.body = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     or isinstance(n, ast.Assign)]
        ns = {}
        exec(compile(tree, "db_probe", "exec"), ns)
        return ns

    def test_db_driver_only_fixed_selects_and_readonly_session_transaction(self):
        ns = self.db_namespace()
        ns["CONFIG_PATH_RESOLUTION"] = "explicit_fixed_config_path"
        calls = []
        class Transaction:
            async def __aenter__(self): calls.append("enter")
            async def __aexit__(self, *args): calls.append("exit")
        class Connection:
            def transaction(self, **kwargs):
                self_case.assertEqual(kwargs, {"isolation":"repeatable_read","readonly":True})
                return Transaction()
            async def fetchrow(self, sql):
                calls.append(sql)
                return {"transaction_read_only":"on","session_read_only":"on","alembic_exists":True,"tasks_exists":False,"gates_exists":False}
            async def fetch(self, sql):
                calls.append(sql)
                return [{"version_num":"0019_digital_employee_agents"}]
            async def close(self, **kwargs): calls.append("closed")
        async def connect(**kwargs):
            self.assertEqual(kwargs["server_settings"]["default_transaction_read_only"], "on")
            self.assertEqual(kwargs["timeout"], 5)
            self.assertEqual(kwargs["command_timeout"], 3)
            return Connection()
        self_case = self
        with mock.patch.dict(sys.modules, {"asyncpg":types.SimpleNamespace(connect=connect)}):
            result = asyncio.run(ns["query"]("postgresql://"+SECRET))
        m.clean_db(result)
        sql = [s for s in calls if s.startswith("SELECT")]
        self.assertEqual(len(sql), 2)
        self.assertTrue(all("INSERT" not in s and "UPDATE" not in s and "CREATE" not in s for s in sql))
        self.assertEqual(result["alembic_versions"], ["0019_digital_employee_agents"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_config_resolver_requires_code_and_resolves_only_selected_db(self):
        ns = self.db_namespace()
        cfg = {"database":{"backend":"postgres", "postgres_url":"$TEST_DB_URL"}, "other":{"secret":SECRET}}
        def read(name, limit): return b"source" if name in m.CONFIG_CODE else b"config"
        ns["read_file"] = read
        ns["CODE"] = {p:m.digest(b"source") for p in m.CONFIG_CODE}
        with mock.patch.dict(sys.modules, {"yaml":types.SimpleNamespace(safe_load=lambda raw:cfg)}), \
             mock.patch.dict(os.environ, {"TEST_DB_URL":"postgresql://"+SECRET,"DEER_FLOW_CONFIG_PATH":"/app/backend/config.yaml"}, clear=True), \
             mock.patch.object(os, "getcwd", return_value="/app/backend"), mock.patch.object(os.path,"lexists",return_value=False):
            self.assertEqual(ns["configured_url"](), "postgresql://"+SECRET)
            cfg["database"]["backend"] = "sqlite"
            with self.assertRaisesRegex(ns["Closed"],"database_not_postgres"): ns["configured_url"]()

    def test_explicit_path_independent_of_exec_cwd_and_project_root(self):
        ns = self.db_namespace()
        ns["CODE"] = {}
        ns["read_file"] = lambda *args:b"config"
        config = {"database":{"backend":"postgres","postgres_url":"postgresql://"+SECRET}}
        with mock.patch.dict(sys.modules,{"yaml":types.SimpleNamespace(safe_load=lambda raw:config)}), \
             mock.patch.dict(os.environ,{"DEER_FLOW_CONFIG_PATH":"/app/backend/config.yaml","DEER_FLOW_PROJECT_ROOT":"/other"},clear=True), \
             mock.patch.object(os,"getcwd",return_value="/app"):
            self.assertEqual(ns["configured_url"](),"postgresql://"+SECRET)
            del os.environ["DEER_FLOW_CONFIG_PATH"]
            with self.assertRaisesRegex(ns["Closed"],"nonstandard_project_root"): ns["configured_url"]()
            del os.environ["DEER_FLOW_PROJECT_ROOT"]
            self.assertEqual(ns["configured_url"](),"postgresql://"+SECRET)
            self.assertEqual(ns["CONFIG_PATH_RESOLUTION"],"known_launcher_runtime_cwd")
            with mock.patch.object(os,"getcwd",return_value="/unknown"):
                with self.assertRaisesRegex(ns["Closed"],"default_config_path_not_proven"): ns["configured_url"]()

    def test_known_gateway_launcher_and_default_cwd_authority(self):
        deployment=Kube().deployments["gateway"]
        image={"status":"COMPLETE","working_directory":"/app","default_launcher":"GATEWAY_BACKEND_CONFIG_DEFAULT"}
        self.assertTrue(m.application_env_authority(deployment,image))
        self.assertTrue(m.default_config_authority(h,deployment,image))
        self.assertFalse(m.default_config_authority(h,deployment,{}))
        container=deployment["spec"]["template"]["spec"]["containers"][0]
        container["args"][0]+=" && export DATABASE_URL="+SECRET
        self.assertFalse(m.application_env_authority(deployment,image))
        with mock.patch.object(h,"run_bounded") as run:
            result=m.exec_probe(h,{"name":"gateway-1","ready":True},"gateway","database",[])
        run.assert_not_called()
        self.assertEqual(result["code"],"application_environment_not_proven")
        self.assertEqual(m.launcher(["docker-entrypoint.sh"],["sh","-c",m.GATEWAY_SHELL],"/app"),"CUSTOM_REDACTED")
        self.assertEqual(m.launcher(["sh","-c"],[m.GATEWAY_SHELL+" --workers ${GATEWAY_WORKERS:-1}"],"/app"),"CUSTOM_REDACTED")

    def test_uv_env_file_cannot_change_application_db_environment(self):
        ns=self.db_namespace()
        ns["read_file"] = mock.Mock(side_effect=AssertionError("must refuse before file read"))
        with mock.patch.dict(os.environ,{"UV_ENV_FILE":SECRET},clear=True):
            with self.assertRaisesRegex(ns["Closed"],"uv_env_file_not_supported"):
                ns["configured_url"]()
        ns["read_file"].assert_not_called()
        for name in ("UV_WORKING_DIR","UV_PROJECT","UV_CONFIG_FILE"):
            with mock.patch.dict(os.environ,{name:SECRET},clear=True):
                with self.assertRaisesRegex(ns["Closed"],"uv_runtime_override_not_supported"):
                    ns["configured_url"]()
        ns["read_file"].assert_not_called()

    def test_frontend_exact_captured_shell_and_entrypoint_projection(self):
        for entrypoint in ([],["docker-entrypoint.sh"],["/usr/local/bin/docker-entrypoint.sh"]):
            self.assertEqual(m.launcher(entrypoint,["sh","-c",m.FRONTEND_SHELL],"/app"),"PNPM_START")
            self.assertEqual(m.launcher(entrypoint,["sh","-c",m.FRONTEND_SHELL+"; echo "+SECRET],"/app"),"CUSTOM_REDACTED")

    def test_db_and_source_probe_python37_syntax_and_no_business_imports(self):
        for text in (m.db_source().decode(), m.source_script(h,"gateway").decode(), (ROOT/"collector.py").read_text()):
            tree = ast.parse(text, feature_version=(3, 7))
            modules = [n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
            self.assertFalse(any(x and x.split(".")[0] in ("deerflow","app") for x in modules))

    @unittest.skipUnless(os.name == "posix", "Exact remote probes require POSIX alarm/uname")
    def test_real_embedded_probes_offline_posix(self):
        for component in ("gateway", "ppt-master-runner"):
            result = subprocess.run([sys.executable,"-I","-B","-"],input=m.source_script(h,component),capture_output=True,timeout=10)
            self.assertEqual(result.returncode,0)
            m.clean_sources(h,json.loads(result.stdout),component)
        result = subprocess.run([sys.executable,"-I","-B","-"],input=m.db_source(),capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0)
        value=m.clean_db(json.loads(result.stdout))
        self.assertEqual(value["status"],"NOT_COLLECTED")
        self.assertEqual(result.stderr,b"")


if __name__ == "__main__":
    unittest.main(verbosity=2)
