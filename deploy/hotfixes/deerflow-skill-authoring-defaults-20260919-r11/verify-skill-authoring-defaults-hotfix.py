#!/usr/bin/env python3
from __future__ import print_function

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path


REQUIRED = {
    "Dockerfile.frontend",
    "Dockerfile.gateway",
    "Dockerfile.weknora-frontend",
    "IMAGE_LOCKS.env",
    "README.txt",
    "SOURCE.COMMIT",
    "SKILLHUB_BUILTIN_DESCRIPTIONS.tsv",
    "VERIFICATION.txt",
    "build-stage-datax-01.sh",
    "extract_model_config.py",
    "import-skill-authoring-defaults-images-node.sh",
    "live_model_runtime_probe.py",
    "manage-skill-creator-datax-01.sh",
    "manage-skillhub-descriptions-datax-01.sh",
    "manage-weknora-model-datax-01.sh",
    "model_runtime_probe_body.py",
    "patch_deerflow_model_config.py",
    "patch_frontend_default_flash.mjs",
    "patch_frontend_default_flash.py",
    "patch_gateway_skill_authoring.py",
    "patch_skill_creator.py",
    "rollback-skill-authoring-defaults-datax-01.sh",
    "run-skill-authoring-defaults-datax-01.sh",
    "shared_model_secret.py",
    "skillhub_descriptions.py",
    "status-skill-authoring-defaults-datax-01.sh",
    "tests/test_hotfix_contracts.py",
    "verify-skill-authoring-defaults-hotfix.py",
    "weknora-sidebar-labels.js",
    "weknora_model_config.py",
}


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            value.update(chunk)
    return value.hexdigest()


def parse_host_source(source, filename):
    if sys.version_info < (3, 8):
        return ast.parse(source, filename=filename)
    if sys.version_info < (3, 9):
        return ast.parse(source, filename=filename, feature_version=7)
    return ast.parse(source, filename=filename, feature_version=(3, 7))


def read_manifest(path):
    rows = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for raw in handle:
            if raw.endswith("\r\n"):
                raise AssertionError("PACKAGE.SHA256 must use LF line endings")
            line = raw.rstrip("\n")
            if not line:
                continue
            checksum, relative = line.split("  ", 1)
            if relative in rows:
                raise AssertionError("duplicate package entry: " + relative)
            rows[relative] = checksum
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()

    missing = sorted(relative for relative in REQUIRED if not (root / relative).is_file())
    if missing:
        raise SystemExit("missing required files: " + ", ".join(missing))
    source_commit = (root / "SOURCE.COMMIT").read_text(encoding="ascii").strip()
    assert re.match(r"^[0-9a-f]{40}$", source_commit)

    package = read_manifest(root / "PACKAGE.SHA256")
    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "PACKAGE.SHA256" and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    assert set(package) == actual_files, "PACKAGE.SHA256 does not cover the exact payload"
    for relative, expected in package.items():
        assert digest(root / relative) == expected, "package digest mismatch: " + relative

    for relative in REQUIRED:
        path = root / relative
        if path.suffix == ".py":
            parse_host_source(path.read_text(encoding="utf-8"), str(path))
        if path.suffix in {".py", ".mjs", ".sh", ".txt", ".env", ".tsv"} or path.name.startswith("Dockerfile."):
            assert b"\r\n" not in path.read_bytes(), relative + " has CRLF line endings"

    run = (root / "run-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
    rollback = (root / "rollback-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
    status = (root / "status-skill-authoring-defaults-datax-01.sh").read_text(encoding="utf-8")
    gateway_patch = (root / "patch_gateway_skill_authoring.py").read_text(encoding="utf-8")
    frontend_patch = (root / "patch_frontend_default_flash.py").read_text(encoding="utf-8")
    frontend_runtime_patch = (root / "patch_frontend_default_flash.mjs").read_text(encoding="utf-8")
    frontend_dockerfile = (root / "Dockerfile.frontend").read_text(encoding="utf-8")
    weknora_sidebar = (root / "weknora-sidebar-labels.js").read_text(encoding="utf-8")
    model_patch = (root / "patch_deerflow_model_config.py").read_text(encoding="utf-8")
    secret_helper = (root / "shared_model_secret.py").read_text(encoding="utf-8")
    weknora_manager = (root / "manage-weknora-model-datax-01.sh").read_text(encoding="utf-8")
    skill_manager = (root / "manage-skill-creator-datax-01.sh").read_text(encoding="utf-8")
    skillhub_manager = (root / "manage-skillhub-descriptions-datax-01.sh").read_text(encoding="utf-8")
    skillhub_sql = (root / "skillhub_descriptions.py").read_text(encoding="utf-8")
    importer = (root / "import-skill-authoring-defaults-images-node.sh").read_text(encoding="utf-8")
    locks = (root / "IMAGE_LOCKS.env").read_text(encoding="utf-8")

    endpoint = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"
    assert endpoint in gateway_patch and endpoint in model_patch and endpoint in status
    assert "expected one %s model item" in model_patch
    assert "unknown production model line" in model_patch
    assert "max_tokens: 32768" in model_patch
    assert "EXPECTED_FILE_SHA256" in (root / "extract_model_config.py").read_text(encoding="utf-8")
    assert "DeepSeek_V4_API_KEY" in secret_helper
    assert "deer-flow-env.before.json" in run and "deer-flow-env.before.json" in rollback
    assert "weknora-deepseek-model.before.json" in run
    assert "manage-weknora-model-datax-01.sh" in run + rollback + status
    assert "PPT_MASTER_DEEPSEEK_UPSTREAM_URL" in run + rollback + status
    assert "deer-flow-primary-model" not in run + rollback + status
    assert "DEERFLOW_PRIMARY_MODEL_API_KEY" not in run + rollback + status
    assert "automatic_rollback" in run and "ROLLBACK.SHA256" in run + rollback
    assert "post_backup_activity_race_recheck=PASS" in run
    assert "pre_workload_activity_race_recheck=PASS" in run
    assert "active_deerflow_runs" in run and "active_oa_jobs" in run
    assert "production_weknora_database_mutation=NONE" in run
    assert "weknora_unified_deepseek_model_restore=PASS" in weknora_manager
    assert "CNOOCCLAW_EXTERNAL_AGENT_KEYS" in frontend_patch
    assert "LEGACY_EXTERNAL_AGENT_KEYS" in frontend_patch
    assert "邮件智能整理助手" in frontend_patch and "邮件智能整理助手" in gateway_patch
    assert "https://cnoocclaw.cnooc.com.cn/yjzs/" in frontend_patch
    assert "CNOOCCLAW_EXTERNAL_AGENT_KEYS" in frontend_runtime_patch
    assert "LEGACY_EXTERNAL_AGENT_KEYS" in frontend_runtime_patch
    assert "邮件智能整理助手" in frontend_runtime_patch
    assert "https://cnoocclaw.cnooc.com.cn/yjzs/" in frontend_runtime_patch
    assert "node /tmp/patch_frontend_default_flash.mjs" in frontend_dockerfile
    assert "python3" not in frontend_dockerfile
    assert '"nav-creatChat"' in weknora_sidebar and '"nav-agents"' in weknora_sidebar
    assert '"\\u65b0\\u5bf9\\u8bdd": "\\u77e5\\u8bc6\\u95ee\\u7b54"' in weknora_sidebar
    assert '"\\u667a\\u80fd\\u4f53": "\\u81ea\\u5b9a\\u4e49\\u95ee\\u7b54"' in weknora_sidebar
    assert 'mode: "flash"' in frontend_patch
    assert "SKILL_AUTHORING_TOOLS" in gateway_patch
    assert "skill_write_file" in gateway_patch
    assert "skillhub_database_schema=PASS" in skillhub_manager
    assert "--schema-sql" in skillhub_manager + skillhub_sql
    assert "d.skill_id = s.id" in skillhub_sql
    assert "JOIN skill s ON s.id = d.skill_id" in skillhub_sql
    assert "d.slug" not in skillhub_sql
    assert "d.namespace_slug" not in skillhub_sql
    assert "d.owner_id" not in skillhub_sql
    assert "deerflow-hotfix-skillhub-zh-r1" not in skillhub_sql
    assert "BASE_GATEWAY_IMAGE='deerflow.local/deer-flow-gateway:20260918-enterprise-arm64-rc15-media-generation1'" in locks
    assert "TARGET_GATEWAY_IMAGE=" in locks and "TARGET_FRONTEND_IMAGE=" in locks
    assert "BASE_WEKNORA_FRONTEND_IMAGE=" in locks and "TARGET_WEKNORA_FRONTEND_IMAGE=" in locks
    assert "weknora-frontend.before.json" in run and "OLD_WEKNORA_FRONTEND_IMAGE" in run + rollback
    assert "weknora_sidebar_asset_runtime=PASS" in status
    assert "deerflow-skill-authoring-defaults-20260919-r11" in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260919-r10" not in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260919-r9" not in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260919-r4" not in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260919-r3" not in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260918-r1" not in run + rollback + skill_manager + weknora_manager
    assert "deerflow-skill-authoring-defaults-20260918-r2" not in run + rollback + skill_manager + weknora_manager
    assert "ReadWriteMany" in skill_manager and "SKILLS_PVC_NAME" in skill_manager
    assert "claimName: deer-flow-skills-pvc" not in skill_manager
    assert "eligible_nodes gateway" in run and "eligible_nodes general" in run
    assert "expanded_gateway_topology=PASS" in run
    assert "gateway_ready_endpoints" in status
    assert "ppt_runner_execution_lease_fd_preserved=PASS mode=a+" in run + status
    assert "_open_lock_file" in run and "_open_lock_file" in status
    assert "inspect.getsource(_lock_file_shared)" not in run + status
    assert "weknora-app.target.patch.json" not in run
    assert "weknora-app.rollback.patch.json" not in rollback
    assert "patch deployment weknora-app" not in run + rollback
    assert "weknora_app_restart=SKIPPED_DATABASE_CONFIGURATION_IS_LIVE" in run + status
    assert "pod_file_check frontend_default_flash" in status
    assert "pod_file_check weknora_index_script" in status
    assert "nginx_fetch weknora_frontend_proxy" in status
    assert "nginx_expect_http_status weknora_backend_runtime" in status
    assert "200,401" in status
    assert "http_status=${status}" in status
    assert "for attempt in $(seq 1 24)" in status
    assert "--gateway-only" in importer
    assert 'REQUESTED_ARTIFACT_DIR="${1:-}"' in importer
    assert 'ARTIFACT_DIR="${REQUESTED_ARTIFACT_DIR}"' in importer
    assert (run + status).count("grep -Fq") >= 11
    assert "grep -q '\\\\u77e5" not in run + status
    assert "grep -q '\\\\u81ea" not in run + status
    assert "--oci-layout" in (root / "build-stage-datax-01.sh").read_text(encoding="utf-8")
    assert not (root / "DeepSeek-V4-Flash-stream-thinking-on-off.py").exists()

    completed = subprocess.run(
        [sys.executable, str(root / "tests/test_hotfix_contracts.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    if completed.returncode:
        raise SystemExit(completed.stdout)
    print(completed.stdout, end="")
    print("deerflow_skill_authoring_defaults_r11_static_contracts=PASS")
    print("frontend_base_runtime_patch_engine=NODE")
    print("ppt_runner_execution_lease_runtime_mode_probe=PASS")
    print("relocatable_image_import_directory_contract=PASS")
    print("production_model_block_structural_match_contract=PASS")
    print("weknora_unicode_literal_probe_contract=PASS")
    print("skillhub_live_schema_and_skill_id_relation_contract=PASS")
    print("skillhub_updated_by_foreign_key_preservation_contract=PASS")
    print("host_python_37_runtime_api_compatibility=PASS")
    print("model_probe_json_stdin_transport=PASS")
    print("probe_container_command_expansion_scope=PASS")
    print("action_specific_skill_authoring_schema=PASS")
    print("default_unset_chat_mode=FLASH")
    print("skillhub_builtin_description_localization=PASS count=17")
    print("unified_deepseek_configuration_contract=PASS consumers=deerflow,weknora,ppt-master-relay")
    print("shared_api_key_secret_atomic_backup_restore=PASS")
    print("mail_digital_employee_direct_link_contract=PASS")
    print("six_current_cnoocclaw_and_five_legacy_alias_separation=PASS")
    print("weknora_sidebar_navigation_labels=PASS new_chat=知识问答 agents=自定义问答")
    print("combined_automatic_rollback_contract=PASS")
    print("expanded_gateway_two_replica_rollout_contract=PASS")
    print("scheduler_eligible_image_probe_contract=PASS")
    print("nfs_rwx_skill_mutation_contract=PASS")
    print("ppt_runner_execution_lease_repair_preservation_contract=PASS")
    print("weknora_database_only_no_restart_contract=PASS")
    print("named_bounded_live_status_diagnostics=PASS")
    print("weknora_authenticated_health_status_contract=PASS accepted=200,401")
    print("rollout_active_work_gate=ZERO_REQUIRED")
    print("production_deerflow_database_mutation_code=NONE")
    print("production_weknora_database_mutation_scope=models.parameters")
    print("production_skillhub_database_mutation_scope=17_builtin_descriptions")
    print("existing_sandbox_mutation_code=NONE")


if __name__ == "__main__":
    main()
