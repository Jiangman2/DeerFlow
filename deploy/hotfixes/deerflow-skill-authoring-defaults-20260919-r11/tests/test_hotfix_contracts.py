from __future__ import print_function

import ast
import base64
import importlib.util
import hashlib
import json
import io
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / relative))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load " + relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HotfixContractTests(unittest.TestCase):
    def test_verifier_uses_native_python37_parse_api(self):
        module = load_module("verify_package", "verify-skill-authoring-defaults-hotfix.py")
        native_parse = ast.parse

        def legacy_parse(source, filename="<unknown>", mode="exec"):
            return native_parse(source, filename=filename, mode=mode)

        with patch.object(module.sys, "version_info", (3, 7, 9)):
            with patch.object(module.ast, "parse", legacy_parse):
                module.parse_host_source("value = 1", "fixture.py")

    def test_operator_model_file_parses_without_posonlyargs(self):
        module = load_module("extract_config", "extract_model_config.py")
        fixture = (
            'client = OpenAI(api_key="' + "x" * 40 + '", base_url="' + module.EXPECTED_BASE_URL + '")\n'
            'def chat_stream(model="DeepSeek-V4-Flash", enable_thinking=True, temperature=0.0):\n'
            '    return client.chat.completions.create(stream=True, extra_body={"enable_thinking": enable_thinking})\n'
        )
        tree = ast.parse(fixture)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and hasattr(node.args, "posonlyargs"):
                del node.args.posonlyargs
        with tempfile.TemporaryDirectory() as value:
            path = Path(value) / "operator-model.py"
            path.write_text(fixture, encoding="utf-8")
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            with patch.object(module, "EXPECTED_FILE_SHA256", checksum):
                with patch.object(module.ast, "parse", return_value=tree):
                    runtime = module.load_runtime(path)
            self.assertEqual(runtime["model"], "DeepSeek-V4-Flash")
            self.assertTrue(runtime["supports_thinking_toggle"])

    def test_model_runtime_is_json_stdin_not_generated_python(self):
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        body = (ROOT / "model_runtime_probe_body.py").read_text(encoding="utf-8")
        self.assertNotIn("printf 'RUNTIME = '", run)
        self.assertIn('RUNTIME = json.load(sys.stdin)', body)
        self.assertIn('.venv/bin/python -c', run)

    def test_container_command_substitution_is_not_expanded_on_host(self):
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertIn(r'"\$(grep -R -l', run)

    def test_model_probes_bypass_proxy_and_disable_redirects(self):
        for name in ("model_runtime_probe_body.py", "live_model_runtime_probe.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("trust_env=False", source)
            self.assertIn("follow_redirects=False", source)
            self.assertIn("max_retries=0", source)

    def test_generated_probe_keeps_frontend_command_in_container(self):
        bash = os.environ.get("HOTFIX_TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        available = subprocess.run([bash, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if available.returncode:
            self.skipTest("bash entry point is present but its runtime is unavailable")
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        manifest = run.split('cat <<YAML | kubectl -n "${NS}" apply -f -\n', 1)[1].split('\nYAML\n', 1)[0]
        script = (
            'set -eu\nTEMP_POD=fixture\nnode=datax-03\nimage=gateway-fixture\n'
            'TARGET_GATEWAY_IMAGE=gateway-fixture\nTARGET_FRONTEND_IMAGE=frontend-fixture\n'
            'TARGET_WEKNORA_FRONTEND_IMAGE=weknora-fixture\nTARGET_PPT_UPSTREAM_URL=https://example.test/v1/chat/completions\n'
            'cat <<YAML\n' + manifest + '\nYAML\n'
        )
        completed = subprocess.run([bash, "-s"], input=script.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(completed.stderr, b"", "probe tried to execute container paths on the host")
        rendered = completed.stdout.decode("utf-8")
        self.assertIn("gateway_skill_authoring_model_mail_agent_and_media_base=PASS", rendered)
        self.assertIn("nodeName: datax-03", rendered)

    def test_model_probe_executes_json_input_without_network_or_key_output(self):
        calls = []
        client_options = []
        http_options = []

        def create(**kwargs):
            calls.append(kwargs)
            return [types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason="stop")])]

        def make_openai(**kwargs):
            client_options.append(kwargs)
            return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))

        def make_http(**kwargs):
            http_options.append(kwargs)
            return object()

        payload = {
            "api_key": "x" * 40,
            "base_url": "https://example.test/v1",
            "file_sha256": "0" * 64,
            "model": "DeepSeek-V4-Flash",
            "stream": True,
            "supports_thinking_toggle": True,
            "temperature": 0.0,
        }
        modules = {
            "httpx": types.SimpleNamespace(Client=make_http),
            "openai": types.SimpleNamespace(OpenAI=make_openai),
        }
        output = io.StringIO()
        with patch.dict(sys.modules, modules):
            with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
                with redirect_stdout(output):
                    runpy.run_path(str(ROOT / "model_runtime_probe_body.py"))
        self.assertEqual([call["extra_body"]["enable_thinking"] for call in calls], [False, True])
        self.assertEqual(client_options[0]["max_retries"], 0)
        self.assertFalse(http_options[0]["trust_env"])
        self.assertFalse(http_options[0]["follow_redirects"])
        self.assertNotIn(payload["api_key"], output.getvalue())

    def test_host_helper_modules_import_on_current_interpreter(self):
        container_only = {"model_runtime_probe_body.py", "live_model_runtime_probe.py"}
        for path in ROOT.glob("*.py"):
            if path.name not in container_only:
                load_module("host_" + path.stem.replace("-", "_"), path.name)

    def test_model_config_patch_is_idempotent_and_has_thinking_toggle(self):
        module = load_module("patch_model", "patch_deerflow_model_config.py")
        patched = module.patch_text("models:\n" + module.OLD_BLOCK + "tool_groups:\n")
        self.assertEqual(module.patch_text(patched), patched)
        self.assertIn("temperature: 0.0", patched)
        self.assertIn("enable_thinking: true", patched)
        self.assertIn("enable_thinking: false", patched)
        self.assertNotIn("thinking_budget", patched)

    def test_model_config_patch_accepts_exact_production_20260919_shape(self):
        module = load_module("patch_model_production", "patch_deerflow_model_config.py")
        production = """config_version: 28
models:
- name: deepseek-v4-flash
  display_name: DeepSeek-V4-Flash
  use: deerflow.models.robust_openai:RobustChatOpenAI
  cumulative_stream_usage: true
  model: DeepSeek-V4-Flash
  api_key: $DeepSeek_V4_API_KEY
  base_url: http://model.ai.cnooc/member1/deepseek-v4-flash-291b-1m/v1
  max_tokens: 32768
  timeout: 600
  profile:
    max_input_tokens: 1048576
  reasoning_effort: high
  supports_thinking: true
  supports_vision: false
  when_thinking_enabled:
    extra_body:
      enable_thinking: true
      thinking_budget: 4096
tool_groups:
- name: web
"""
        patched = module.patch_text(production)
        self.assertIn(module.TARGET_BASE_URL, patched)
        self.assertIn("  max_tokens: 65536", patched)
        self.assertIn("  temperature: 0.0", patched)
        self.assertIn("  when_thinking_disabled:", patched)
        self.assertNotIn("thinking_budget", patched)
        self.assertIn("tool_groups:\n- name: web\n", patched)

    def test_model_config_patch_fails_closed_on_unknown_model_field(self):
        module = load_module("patch_model_unknown", "patch_deerflow_model_config.py")
        source = "models:\n" + module.OLD_BLOCK.replace(
            "  timeout: 600\n", "  timeout: 600\n  unreviewed_option: true\n"
        )
        with self.assertRaisesRegex(ValueError, "unknown production model line"):
            module.patch_text(source)

    def test_model_config_patch_requires_unique_named_model(self):
        module = load_module("patch_model_duplicate", "patch_deerflow_model_config.py")
        source = "models:\n" + module.OLD_BLOCK + module.OLD_BLOCK
        with self.assertRaisesRegex(ValueError, "expected one deepseek-v4-flash model item"):
            module.patch_text(source)

    def test_shared_secret_patch_preserves_secret_shape(self):
        module = load_module("shared_secret", "shared_model_secret.py")
        old = base64.b64encode(b"x" * 40).decode("ascii")
        document = {"metadata": {"name": "deer-flow-env"}, "data": {module.SECRET_KEY: old}}
        self.assertEqual(module._encoded_value(document), old)
        patch = json.loads(module._merge_patch(old))
        self.assertEqual(patch, {"data": {module.SECRET_KEY: old}})

    def test_frontend_patcher_splits_current_and_legacy_allowlists(self):
        module = load_module("patch_frontend", "patch_frontend_default_flash.py")
        with tempfile.TemporaryDirectory() as value:
            root = Path(value) / "python"
            node_root = Path(value) / "node"
            paths = [
                "src/core/settings/local.ts",
                "src/components/workspace/mode-selector.tsx",
                "src/components/workspace/sidecar/sidecar-panel.tsx",
                "src/core/agents/shared-agents.ts",
                "src/core/agents/external-launch.ts",
            ]
            for relative in paths:
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / paths[0]).write_text(
                '''    mode: undefined,
}

export function mergeLocalSettings(
  settings?: Partial<LocalSettings>,
      const settings = JSON.parse(json) as Partial<LocalSettings>;
''',
                encoding="utf-8",
            )
            for relative in paths[1:3]:
                (root / relative).write_text('  return supportsThinking ? "pro" : "flash";\n', encoding="utf-8")
            (root / paths[3]).write_text(
                '''  {
    name: "前期研究报告编写专家",
    description: "科研报告智能生产专家，支持科研内容组织、撰写与输出优化。",
    model: null,
    tool_groups: ["报告生成", "PPT to Word"],
    skills: null,
    category_id: "science-technology-innovation",
    tags: ["科研报告", "内容生成", "PPT"],
    avatar_id: "knowledge-service",
    source: "team",
    external_url: "https://cnoocclaw.cnooc.com.cn/qqyjbgbxzj/",
  },
];
''',
                encoding="utf-8",
            )
            (root / paths[4]).write_text(
                '''const EXTERNAL_AGENT_KEYS = [
  "zywdjxzj",
  "qqyjbgbxzj",
  "zjbgjszj",
  "zdsczj",
  "dzsjtpzj",
] as const;
    const isApprovedAgent = EXTERNAL_AGENT_KEYS.some(
      (candidate) => candidate === key,
    );
      isApprovedAgent
    ) {
      return value;
      isApprovedAgent
    ) {
      return `/api/cnooc/external-agents/${key}/launch`;
''',
                encoding="utf-8",
            )
            shutil.copytree(str(root), str(node_root))
            module.patch(root)
            node = shutil.which("node")
            if node is not None:
                completed = subprocess.run(
                    [
                        node,
                        str(ROOT / "patch_frontend_default_flash.mjs"),
                        "--frontend-root",
                        str(node_root),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertIn("frontend_default_flash_patch=PASS", completed.stdout)
                for relative in paths:
                    self.assertEqual(
                        (root / relative).read_bytes(),
                        (node_root / relative).read_bytes(),
                        relative,
                    )
            launch = (root / paths[4]).read_text(encoding="utf-8")
            agents = (root / paths[3]).read_text(encoding="utf-8")
            self.assertIn('"yjzs"', launch)
            self.assertIn("CNOOCCLAW_EXTERNAL_AGENT_KEYS", launch)
            self.assertIn("LEGACY_EXTERNAL_AGENT_KEYS", launch)
            legacy = launch.split("const LEGACY_EXTERNAL_AGENT_KEYS", 1)[1].split("] as const", 1)[0]
            self.assertNotIn("yjzs", legacy)
            self.assertIn("邮件智能整理助手", agents)
            self.assertIn("https://cnoocclaw.cnooc.com.cn/yjzs/", agents)

    def test_frontend_image_patch_uses_available_node_runtime(self):
        dockerfile = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
        runtime_patch = (ROOT / "patch_frontend_default_flash.mjs").read_text(encoding="utf-8")
        self.assertIn("node /tmp/patch_frontend_default_flash.mjs", dockerfile)
        self.assertNotIn("python3", dockerfile)
        self.assertIn("CNOOCCLAW_EXTERNAL_AGENT_KEYS", runtime_patch)
        self.assertIn("LEGACY_EXTERNAL_AGENT_KEYS", runtime_patch)
        self.assertIn("邮件智能整理助手", runtime_patch)
        self.assertIn('mode: "flash"', runtime_patch)

    def test_rollout_is_zero_activity_and_has_exact_rollback_inputs(self):
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        rollback = (ROOT / "rollback-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertIn("active_deerflow_runs", run)
        self.assertIn("active_oa_jobs", run)
        self.assertIn("post_backup_activity_race_recheck=PASS", run)
        self.assertIn("pre_workload_activity_race_recheck=PASS", run)
        self.assertIn("deer-flow-env.before.json", run)
        self.assertIn("weknora-deepseek-model.before.json", run)
        self.assertIn("automatic_rollback", run)
        self.assertIn("shared_model_secret.py", rollback)
        self.assertIn("manage-weknora-model-datax-01.sh", rollback)
        self.assertIn("weknora-frontend.before.json", run)
        self.assertIn("OLD_WEKNORA_FRONTEND_IMAGE", run + rollback)
        self.assertIn("TARGET_WEKNORA_FRONTEND_IMAGE", run)
        self.assertNotIn("deer-flow-primary-model", run + rollback)
        self.assertNotIn("DEERFLOW_PRIMARY_MODEL_API_KEY", run + rollback)

    def test_expanded_production_topology_and_rwx_contracts(self):
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        status = (ROOT / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        manager = (ROOT / "manage-skill-creator-datax-01.sh").read_text(encoding="utf-8")
        importer = (ROOT / "import-skill-authoring-defaults-images-node.sh").read_text(encoding="utf-8")
        self.assertIn('gateway["spec"].get("replicas")==2', run)
        self.assertIn("maxUnavailable", run)
        self.assertIn("eligible_nodes gateway", run)
        self.assertIn("eligible_nodes general", run)
        self.assertIn("gateway_ready_endpoints", status)
        self.assertIn("ReadWriteMany", manager)
        self.assertIn("SKILLS_PVC_NAME", manager)
        self.assertNotIn("claimName: deer-flow-skills-pvc", manager)
        self.assertIn("--gateway-only", importer)
        self.assertIn('REQUESTED_ARTIFACT_DIR="${1:-}"', importer)
        self.assertIn('ARTIFACT_DIR="${REQUESTED_ARTIFACT_DIR}"', importer)

    def test_r11_evidence_namespace_and_runner_repair_are_fail_closed(self):
        scripts = [
            ROOT / "run-skill-authoring-defaults-datax-01.sh",
            ROOT / "rollback-skill-authoring-defaults-datax-01.sh",
            ROOT / "manage-skill-creator-datax-01.sh",
            ROOT / "manage-skillhub-descriptions-datax-01.sh",
            ROOT / "manage-weknora-model-datax-01.sh",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in scripts)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260918-r1", combined)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260918-r2", combined)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260919-r3", combined)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260919-r4", combined)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260919-r9", combined)
        self.assertNotIn("deerflow-skill-authoring-defaults-20260919-r10", combined)
        self.assertGreaterEqual(combined.count("deerflow-skill-authoring-defaults-20260919-r11"), 5)
        run = scripts[0].read_text(encoding="utf-8")
        status = (ROOT / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertIn("ppt_runner_execution_lease_fd_preserved=PASS mode=a+", run)
        self.assertIn("ppt_runner_execution_lease_fd_preserved=PASS mode=a+", status)
        self.assertIn("_open_lock_file", run)
        self.assertIn("_open_lock_file", status)
        self.assertIn('mode=="a+"', run)
        self.assertIn('mode=="a+"', status)
        self.assertNotIn("inspect.getsource(_lock_file_shared)", run + status)
        self.assertIn("recent_ppt_runner_bad_file_descriptor=NONE", status)

    def test_weknora_backend_is_not_restarted_for_database_only_configuration(self):
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        rollback = (ROOT / "rollback-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        status = (ROOT / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertNotIn("weknora-app.target.patch.json", run)
        self.assertNotIn("weknora-app.rollback.patch.json", rollback)
        self.assertNotIn("patch deployment weknora-app", run + rollback)
        self.assertNotIn("weknora-app.before.json", run)
        self.assertIn("weknora_app_restart=SKIPPED_DATABASE_CONFIGURATION_IS_LIVE", run)
        self.assertIn("weknora_app_restart=SKIPPED_DATABASE_CONFIGURATION_IS_LIVE", status)
        self.assertIn("weknora_backend_runtime", status)

    def test_status_checks_are_named_and_bounded(self):
        status = (ROOT / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertIn("STATUS PHASE:", status)
        self.assertIn("pod_file_check frontend_default_flash", status)
        self.assertIn("pod_file_check weknora_index_script", status)
        self.assertIn("nginx_fetch weknora_frontend_proxy", status)
        self.assertIn("nginx_expect_http_status weknora_backend_runtime", status)
        self.assertIn("200,401", status)
        self.assertIn("http_status=${status}", status)
        self.assertIn("for attempt in $(seq 1 24)", status)
        self.assertIn("attempts=24", status)

    def test_skillhub_manifest_contains_exactly_17_chinese_descriptions(self):
        rows = []
        for line in (ROOT / "SKILLHUB_BUILTIN_DESCRIPTIONS.tsv").read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(line.split("\t", 1))
        self.assertEqual(len(rows), 17)
        self.assertEqual(len({row[0] for row in rows}), 17)
        self.assertTrue(all(len(row) == 2 and row[1].strip() for row in rows))
        self.assertTrue(all(any("\u4e00" <= char <= "\u9fff" for char in row[1]) for row in rows))

    def test_skillhub_update_uses_live_skill_id_document_relation(self):
        module = load_module("skillhub_sql", "skillhub_descriptions.py")
        rows = module.load_descriptions(ROOT / "SKILLHUB_BUILTIN_DESCRIPTIONS.tsv")
        update = module.emit_update(rows)
        schema = module.emit_schema_check()
        self.assertIn("d.skill_id = s.id", update)
        self.assertIn("JOIN skill s ON s.id = d.skill_id", update)
        self.assertNotIn("d.slug", update)
        self.assertNotIn("d.namespace_slug", update)
        self.assertNotIn("d.owner_id", update)
        self.assertNotIn("deerflow-hotfix-skillhub-zh-r1", update)
        self.assertNotIn("updated_by =", update)
        self.assertIn("('skill_search_document', 'skill_id')", schema)
        self.assertNotIn("('skill_search_document', 'slug')", schema)

    def test_weknora_sidebar_labels_are_selector_scoped(self):
        script = (ROOT / "weknora-sidebar-labels.js").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile.weknora-frontend").read_text(encoding="utf-8")
        run = (ROOT / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        status = (ROOT / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
        self.assertIn('"nav-creatChat"', script)
        self.assertIn('"nav-agents"', script)
        self.assertIn('"\\u65b0\\u5bf9\\u8bdd": "\\u77e5\\u8bc6\\u95ee\\u7b54"', script)
        self.assertIn('"\\u667a\\u80fd\\u4f53": "\\u81ea\\u5b9a\\u4e49\\u95ee\\u7b54"', script)
        self.assertNotIn("document.body.innerHTML", script)
        self.assertIn("FROM weknora-frontend-base", dockerfile)
        self.assertIn("deerflow-sidebar-labels-20260918-r1.js", dockerfile)
        self.assertGreaterEqual((run + status).count("grep -Fq"), 11)
        self.assertNotIn("grep -q '\\\\u77e5", run + status)
        self.assertNotIn("grep -q '\\\\u81ea", run + status)

    def test_host_python_files_parse_as_python_37(self):
        verifier = load_module("verify_host_syntax", "verify-skill-authoring-defaults-hotfix.py")
        for path in ROOT.glob("*.py"):
            verifier.parse_host_source(path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    unittest.main()
