#!/usr/bin/env python3
from __future__ import print_function

import argparse
import ast
import hashlib
import re
import subprocess
import sys
from pathlib import Path


def require(condition, detail):
    if not condition:
        raise SystemExit("digital_employee_domains_package=FAIL detail=" + detail)


def read(relative):
    return (root / relative).read_text(encoding="utf-8")


parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
args = parser.parse_args()
root = Path(args.root).resolve()

required = [
    "Dockerfile.gateway",
    "Dockerfile.frontend",
    "Dockerfile.provisioner",
    "IMAGE_LOCKS.env",
    "SOURCE.COMMIT",
    "SKILL.SHA256",
    "build-stage-datax-01.sh",
    "import-digital-employee-domains-images-node.sh",
    "inspect-digital-employee-domains-datax-01.sh",
    "manage-research-fund-settlement-skill-datax-01.sh",
    "normalize-database-agent-categories.py",
    "normalize-file-agent-categories.py",
    "provisioner_api_request.py",
    "sandbox_api_exec.py",
    "rollback-digital-employee-domains-datax-01.sh",
    "run-digital-employee-domains-datax-01.sh",
    "status-digital-employee-domains-datax-01.sh",
    "tests/test_normalize_file_agent_categories.py",
    "tests/test_sandbox_api_exec.py",
    "payload/backend/app/gateway/app.py",
    "payload/backend/app/gateway/routers/agents.py",
    "payload/backend/packages/harness/deerflow/persistence/agents/base.py",
    "payload/backend/packages/harness/deerflow/persistence/agents/file.py",
    "payload/backend/packages/harness/deerflow/persistence/agents/sql.py",
    "payload/backend/packages/harness/deerflow/skills/installer.py",
    "payload/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py",
    "payload/provisioner/app.py",
    "payload/frontend/src/app/workspace/agents/new/page.tsx",
    "payload/frontend/src/components/workspace/agents/agent-gallery.tsx",
    "payload/frontend/src/components/workspace/agents/business-domain-badge.tsx",
    "payload/frontend/src/components/workspace/agents/business-domain-picker.tsx",
    "payload/frontend/src/components/workspace/agents/digital-employee-settings-dialog.tsx",
    "payload/frontend/src/core/agents/api.ts",
    "payload/frontend/src/core/agents/hooks.ts",
    "payload/frontend/src/core/agents/shared-agents.ts",
    "payload/frontend/src/core/agents/types.ts",
    "payload/frontend/src/core/i18n/locales/en-US.ts",
    "payload/frontend/src/core/i18n/locales/types.ts",
    "payload/frontend/src/core/i18n/locales/zh-CN.ts",
    "payload/skills/public/research-fund-settlement/SKILL.md",
    "payload/skills/public/research-fund-settlement/assets/reference-examples/group-company-example.docx",
    "payload/skills/public/research-fund-settlement/assets/reference-examples/group-company-standard-input.xlsx",
    "payload/skills/public/research-fund-settlement/references/group-company-standard-input.md",
    "payload/skills/public/research-fund-settlement/references/group-company-template-annotations.md",
    "payload/skills/public/research-fund-settlement/scripts/group_build.py",
    "payload/skills/public/research-fund-settlement/scripts/group_template.py",
    "payload/skills/public/research-fund-settlement/scripts/standard_input.py",
]
for relative in required:
    require((root / relative).is_file(), "missing=" + relative)

source_commit = read("SOURCE.COMMIT").strip()
require(bool(re.match(r"^[0-9a-f]{40}$", source_commit)), "invalid_source_commit")

agents_path = root / "payload/backend/app/gateway/routers/agents.py"
agents_source = agents_path.read_text(encoding="utf-8")
ast.parse(agents_source, filename=str(agents_path))
require("CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES" in agents_source, "corporate_taxonomy_missing")
require(agents_source.count('domain_group="unified"') == 17, "unified_domain_count_not_17")
require(agents_source.count('domain_group="distributed"') == 9, "distributed_domain_count_not_9")
require('@router.put(\n    "/admin/digital-employee-categories"' not in agents_source, "admin_mutation_route_present")
require("migrate_digital_employee_categories" in agents_source, "migration_missing")
require("store.patch_config(" in agents_source, "raw_category_patch_missing")
require("store.update(" not in agents_source, "category_migration_model_round_trip_present")

app_path = root / "payload/backend/app/gateway/app.py"
app_source = app_path.read_text(encoding="utf-8")
ast.parse(app_source, filename=str(app_path))
require("agents.migrate_digital_employee_categories" in app_source, "startup_migration_missing")

installer_source = read("payload/backend/packages/harness/deerflow/skills/installer.py")
storage_source = read("payload/backend/packages/harness/deerflow/skills/storage/local_skill_storage.py")
provisioner_source = read("payload/provisioner/app.py")
agent_store_source = read("payload/backend/packages/harness/deerflow/persistence/agents/base.py")
file_agent_store_source = read("payload/backend/packages/harness/deerflow/persistence/agents/file.py")
sql_agent_store_source = read("payload/backend/packages/harness/deerflow/persistence/agents/sql.py")
require("def patch_config(" in agent_store_source, "agent_store_patch_config_compatibility_missing")
require("document.update(updates)" in file_agent_store_source, "file_agent_raw_merge_missing")
require("self._write(config_file.parent, document, None)" in file_agent_store_source, "file_agent_atomic_patch_missing")
require("document = dict(row.config or {})" in sql_agent_store_source, "sql_agent_raw_document_copy_missing")
require("document.update(_config_document(updates))" in sql_agent_store_source, "sql_agent_raw_merge_missing")
require("def build_skill_tree_manifest" in installer_source, "skill_tree_manifest_missing")
require("hashlib.sha256()" in installer_source, "skill_tree_sha256_missing")
require("Skill tree contains unsupported symlink" in installer_source, "skill_tree_symlink_rejection_missing")
require("Archive contains unsupported symlink member" in installer_source, "archive_symlink_rejection_missing")
require(storage_source.count("verify_skill_tree_copy(skill_dir, staging_target)") == 2, "skill_install_and_upgrade_integrity_checks_missing")
require("_move_staged_skill_into_reserved_target(staging_target, target)" in storage_source, "skill_atomic_commit_missing")
require("def _skills_pvc_subpath" in provisioner_source, "provisioner_skills_pvc_subpath_missing")
require('mount_path="/mnt/skills/custom"' in provisioner_source, "provisioner_custom_skill_mount_missing")
require('sub_path=_user_pvc_subpath(user_id, "skills", "custom")' in provisioner_source, "provisioner_user_custom_subpath_missing")
require('name="verify-skill-mounts"' in provisioner_source, "provisioner_skill_mount_verifier_missing")
require("[path.open('rb').read(1) for path in files]" in provisioner_source, "provisioner_skill_file_read_probe_missing")
require("init_containers=[user_data_initializer, skill_mount_verifier]" in provisioner_source, "provisioner_init_order_missing")
for token in (
    'SANDBOX_USER_ANNOTATION = "deerflow.io/user-id"',
    'SANDBOX_THREAD_ANNOTATION = "deerflow.io/thread-id"',
    'SANDBOX_LEGACY_SKILLS_ANNOTATION = "deerflow.io/include-legacy-skills"',
    "def _assert_existing_sandbox_identity(",
    "actual_mounts != expected_mounts",
    "remove the stale Service before reuse",
):
    require(token in provisioner_source, "provisioner_identity_contract_missing=" + token)
require(provisioner_source.count("_assert_existing_sandbox_identity(") >= 3, "provisioner_identity_reuse_guards_incomplete")

normalizer_path = root / "normalize-database-agent-categories.py"
normalizer = normalizer_path.read_text(encoding="utf-8")
ast.parse(normalizer, filename=str(normalizer_path))
require("get_sync_sessionmaker" in normalizer, "explicit_database_session_missing")
require('LOCK TABLE agents IN SHARE ROW EXCLUSIVE MODE' in normalizer, "database_write_race_lock_missing")
require("select(AgentRow).with_for_update()" in normalizer, "database_row_lock_missing")
require("resolve_digital_employee_category" in normalizer, "shared_category_resolver_missing")
require('payload["category_id"] = target' in normalizer, "bounded_category_mutation_missing")
require("database_agent_category_normalization=PASS" in normalizer, "database_normalization_pass_marker_missing")
require('database_backend != "postgres"' in normalizer, "database_normalizer_must_require_postgres")

file_normalizer_path = root / "normalize-file-agent-categories.py"
file_normalizer = file_normalizer_path.read_text(encoding="utf-8")
ast.parse(file_normalizer, filename=str(file_normalizer_path))
require("_write_category_to_config_file" in file_normalizer, "shared_file_category_writer_missing")
require("get_paths" in file_normalizer, "configured_file_agent_root_missing")
require("file_agent_category_normalization=PASS" in file_normalizer, "file_normalization_pass_marker_missing")
file_normalizer_tests = subprocess.run(
    [sys.executable, str(root / "tests/test_normalize_file_agent_categories.py")],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
require(file_normalizer_tests.returncode == 0, "file_normalizer_tests_failed=" + file_normalizer_tests.stdout[-2000:])
sandbox_api_tests = subprocess.run(
    [sys.executable, str(root / "tests/test_sandbox_api_exec.py")],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)
require(sandbox_api_tests.returncode == 0, "sandbox_api_tests_failed=" + sandbox_api_tests.stdout[-2000:])

badge = read("payload/frontend/src/components/workspace/agents/business-domain-badge.tsx")
picker = read("payload/frontend/src/components/workspace/agents/business-domain-picker.tsx")
gallery = read("payload/frontend/src/components/workspace/agents/agent-gallery.tsx")
zh = read("payload/frontend/src/core/i18n/locales/zh-CN.ts")
api = read("payload/frontend/src/core/agents/api.ts")
hooks = read("payload/frontend/src/core/agents/hooks.ts")
require('{isUnified ? "统" : "分"}' in badge, "domain_badge_missing")
require('data-testid="business-domain-picker-list"' in picker, "compact_picker_missing")
require('data-testid="business-domain-directory"' in gallery, "gallery_directory_missing")
require('category: "业务域分类"' in zh, "business_domain_label_missing")
require("replaceDigitalEmployeeCategories" not in api, "frontend_admin_api_present")
require("useReplaceDigitalEmployeeCategories" not in hooks, "frontend_admin_hook_present")

gateway_dockerfile = read("Dockerfile.gateway")
frontend_dockerfile = read("Dockerfile.frontend")
provisioner_dockerfile = read("Dockerfile.provisioner")
require("FROM digital-domains-gateway-base" in gateway_dockerfile, "gateway_base_alias_missing")
require("FROM digital-domains-frontend-base" in frontend_dockerfile, "frontend_base_alias_missing")
require("FROM digital-domains-provisioner-base" in provisioner_dockerfile, "provisioner_base_alias_missing")
require("--network=none" in gateway_dockerfile, "gateway_build_network_not_disabled")
require("--network=none" in frontend_dockerfile, "frontend_build_network_not_disabled")
require("--network=none" in provisioner_dockerfile, "provisioner_build_network_not_disabled")
require("skills/installer.py" in gateway_dockerfile and "local_skill_storage.py" in gateway_dockerfile, "gateway_skill_integrity_overlay_missing")
for relative in ("base.py", "file.py", "sql.py"):
    require("persistence/agents/" + relative in gateway_dockerfile, "gateway_agent_store_overlay_missing=" + relative)
require("category-manager-dialog.tsx" in frontend_dockerfile, "admin_dialog_removal_missing")

locks = read("IMAGE_LOCKS.env")
require(locks.count("digitaldomains-skillruntime12") == 6, "integrated_target_image_locks_incomplete")
require("BASE_PROVISIONER_IMAGE='deerflow.local/deer-flow-provisioner:20260817-enterprise-arm64-rc15-sandboxstorage1'" in locks, "production_provisioner_base_lock_missing")
require("BASE_SKILLS_IMAGE=" in locks, "base_skills_image_lock_missing")
require("BASE_GATEWAY_INIT_IMAGE='deerflow.local/deer-flow-gateway:20260811-enterprise-arm64-rc15-consolidated2'" in locks, "gateway_init_image_lock_missing")
require("20260918-deerflow-digital-domains-skillruntime12" in locks, "integrated_artifact_dir_missing")
build_script = read("build-stage-datax-01.sh")
require('BUILDCTL_BUILD_HELP="$(buildctl build --help 2>&1)"' in build_script, "buildctl_help_capture_missing_in_build")
require('<<<"${BUILDCTL_BUILD_HELP}"' in build_script, "buildctl_help_non_pipeline_match_missing_in_build")
require("buildctl build --help 2>&1 | grep" not in build_script, "pipefail_sigpipe_buildctl_probe_present_in_build")
require("Dockerfile.provisioner" in build_script, "provisioner_image_build_missing")
require("Dockerfile.frontend" in build_script, "frontend_image_build_missing")
require(build_script.count("build_oci ") == 3, "three_target_image_builds_missing")
require("images=gateway,frontend,provisioner" in build_script, "three_image_build_marker_missing")
import_script = read("import-digital-employee-domains-images-node.sh")
require('"${GATEWAY_OCI_NAME}" "${FRONTEND_OCI_NAME}" "${PROVISIONER_OCI_NAME}"' in import_script, "integrated_image_import_set_missing")
for token in ("TARGET_GATEWAY_IMAGE", "TARGET_FRONTEND_IMAGE", "TARGET_PROVISIONER_IMAGE"):
    require(token in import_script, "target_image_import_guard_missing=" + token)

skill_root = root / "payload/skills/public/research-fund-settlement"
skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
require(skill_text.startswith("---\n"), "skill_frontmatter_missing")
frontmatter_end = skill_text.find("\n---\n", 4)
require(frontmatter_end > 4, "skill_frontmatter_not_closed")
frontmatter = skill_text[4:frontmatter_end]
require(re.search(r"(?m)^name:\s*research-fund-settlement\s*$", frontmatter) is not None, "skill_name_invalid")
require(re.search(r"(?m)^description:\s*.+$", frontmatter) is not None, "skill_description_missing")
require("group-company-standard-input.xlsx" in skill_text, "standard_workbook_workflow_missing")
require("group-company-template-annotations.md" in skill_text, "template_annotation_authority_missing")

manifested = set()
for line_number, line in enumerate(read("SKILL.SHA256").splitlines(), 1):
    require(bool(line.strip()), "blank_skill_manifest_line=%d" % line_number)
    match = re.match(r"^([0-9a-f]{64})  (\./.+)$", line)
    require(match is not None, "invalid_skill_manifest_line=%d" % line_number)
    digest, relative = match.groups()
    relative = relative[2:]
    require(relative not in manifested, "duplicate_skill_manifest_path=" + relative)
    path = skill_root / Path(relative)
    require(path.is_file(), "skill_manifest_file_missing=" + relative)
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    require(observed == digest, "skill_manifest_digest_mismatch=" + relative)
    manifested.add(relative)
actual_skill_files = {
    path.relative_to(skill_root).as_posix()
    for path in skill_root.rglob("*")
    if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
}
require(manifested == actual_skill_files, "skill_manifest_coverage_mismatch")

standard_input = read("payload/skills/public/research-fund-settlement/scripts/standard_input.py")
for token in (
    "MAX_ARCHIVE_BYTES = 32 * 1024 * 1024",
    "MAX_ARCHIVE_ENTRIES = 256",
    "MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024",
    "MAX_XML_PART_BYTES = 16 * 1024 * 1024",
    'EXPECTED_DIMENSION = "A1:M36"',
    'SHEET_NAME = "Sheet2"',
    'any(part in {"", ".", ".."} for part in path.parts)',
    "if normalized in normalized_names:",
    "if info.flag_bits & 0x1:",
    "if len(sheets) != 1 or sheets[0].attrib.get(\"name\") != SHEET_NAME:",
    'relationship.attrib.get("Type") != WORKSHEET_REL',
):
    require(token in standard_input, "research_skill_xlsx_guard_missing=" + token)

survey = read("inspect-digital-employee-domains-datax-01.sh")
require('BUILDCTL_BUILD_HELP="$(buildctl build --help 2>&1)"' in survey, "buildctl_help_capture_missing_in_survey")
require('<<<"${BUILDCTL_BUILD_HELP}"' in survey, "buildctl_help_non_pipeline_match_missing_in_survey")
require("buildctl build --help 2>&1 | grep" not in survey, "pipefail_sigpipe_buildctl_probe_present_in_survey")
require("production_mutation=NONE" in survey, "read_only_survey_marker_missing")
require("production_skill_mutated=no" in survey, "read_only_skill_marker_missing")
require("agents_backup_capability=PASS" in survey, "agents_backup_probe_missing")
require("maintenance_gate_ready" in survey, "maintenance_gate_missing")
require("BASE_SKILLS_IMAGE" in survey, "base_skills_image_probe_missing")
require("BASE_PROVISIONER_IMAGE" in survey, "base_provisioner_image_probe_missing")
require('images inspect "${BASE_FRONTEND_IMAGE}"' in survey, "base_frontend_image_probe_missing")
require('images inspect "${TARGET_FRONTEND_IMAGE}"' not in survey, "prebuild_target_frontend_image_probe_present")
require("target_frontend_image_missing_on_datax_01" not in survey, "stale_reused_frontend_survey_guard_present")
require("provisioner_production_environment=PASS" in survey, "survey_provisioner_environment_guard_missing")
require("provisioner_rolling_update_continuity=PASS" in survey, "survey_provisioner_rollout_guard_missing")
require("manage-research-fund-settlement-skill-datax-01.sh" in survey, "live_skill_preflight_missing")
require("service_health_proxy_bypass=PASS" in survey, "survey_proxy_bypass_marker_missing")
require("wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config" in survey, "survey_frontend_loopback_proxy_bypass_missing")

readme = read("README.txt")
require("Reuse the already-built R12 images" in readme, "integrated_image_build_instructions_missing")
require("digitaldomains-skillruntime12" in readme, "integrated_target_images_missing_from_readme")
for stale_text in (
    "Do not rebuild or re-import images for R10",
    "It does not patch Provisioner",
    "R10 combines two compatible changes",
):
    require(stale_text not in readme, "stale_readme_instruction=" + stale_text)

manager = read("manage-research-fund-settlement-skill-datax-01.sh")
for token in ("--backup", "--install", "--restore", "--finalize", "deer-flow-skills-pvc", "SKILL.SHA256"):
    require(token in manager, "skill_manager_contract_missing=" + token)
require('mv -- "${target}" "${backup}"' in manager, "atomic_skill_backup_swap_missing")
require('mv -- "${stage}" "${target}"' in manager, "atomic_skill_install_swap_missing")
require('--wait=true --timeout=120s' in manager, "stale_skill_probe_cleanup_wait_missing")
require("research_fund_settlement_skill_restore=PASS" in manager, "skill_restore_marker_missing")

run = read("run-digital-employee-domains-datax-01.sh")
backup_position = run.find('--backup "${EVIDENCE_DIR}"')
post_backup_recheck_position = run.find("post_backup_activity_race_recheck=PASS")
transaction_position = run.find("TARGET_APPLIED=yes")
install_position = run.find('--install "${EVIDENCE_DIR}"')
pre_provisioner_recheck_position = run.find("pre_provisioner_activity_race_recheck=PASS")
provisioner_position = run.find("set image deployment/provisioner")
canary_position = run.find("skillhub_custom_skill_sandbox_execution_canary=PASS")
pre_gateway_recheck_position = run.find("pre_gateway_activity_race_recheck=PASS")
gateway_position = run.find("set image deployment/gateway")
require(
    0 <= backup_position < post_backup_recheck_position < transaction_position < install_position
    < pre_provisioner_recheck_position < provisioner_position < canary_position
    < pre_gateway_recheck_position < gateway_position,
    "combined_transaction_and_race_recheck_order_invalid",
)
require("research-fund-settlement.before.tar" in run, "skill_rollback_archive_not_checksummed")
require("unmigrated_database_agent_categories" in run, "database_migration_postcheck_missing")
require("normalize-database-agent-categories.py" in run, "backend_independent_database_migration_missing")
require(run.find("normalize-database-agent-categories.py") < run.find("unmigrated_database_agent_categories"), "database_migration_must_precede_postcheck")
require("normalize-file-agent-categories.py" in run, "fail_closed_file_migration_missing")
require(run.find("normalize-file-agent-categories.py") < run.find("unmigrated file agent categories"), "file_migration_must_precede_postcheck")
require("from deerflow.persistence.agents.sql import get_sync_sessionmaker" in run, "target_image_sql_store_probe_missing")
require("from deerflow.persistence.agents.sql import SqlAgentStore" in run, "target_image_sql_store_class_probe_missing")
require("SQLAgentStore" not in run, "invalid_sql_agent_store_class_name_present")
require('assert AgentRow.__tablename__=="agents"' in run, "target_image_agent_model_probe_missing")
require("wait_for_probe" in run, "failure_aware_probe_wait_missing")
require("probe_diagnostics" in run, "probe_failure_diagnostics_missing")
require("Failed:*" in run and "ErrImageNeverPull" in run, "probe_fail_fast_states_missing")
require("configured_agent_storage_backend=" in run, "live_agent_storage_backend_probe_missing")
require("database_agent_category_rows_planned=" in run, "database_category_plan_probe_missing")
require("database_category_normalization_preflight=PASS" in run, "database_normalization_preflight_marker_missing")
require("file_agent_atomic_replace_capability=PASS" in run, "file_atomic_replace_preflight_missing")
require("file_agent_category_read_only_target_image_preflight=PASS" in run, "target_image_file_config_preflight_missing")
require("claimName: deer-flow-userdata-pvc" in run and "readOnly: true" in run, "target_image_file_config_preflight_not_read_only")
require(
    "mountPath: /app/backend/.deer-flow\n          readOnly: true\n          subPath: deer-flow" in run,
    "target_image_file_config_preflight_missing_production_subpath",
)
require("file_agent_category_migration=PASS" in run, "file_migration_postcheck_missing")
require("research_fund_settlement_runtime=CHECK_BUILD_INSPECT_PASS" in run, "skill_runtime_postcheck_missing")
require("active_deerflow_runs" in run and "active_oa_jobs" in run, "zero_activity_rollout_gate_missing")
require('"seed-extensions-config=${TARGET_GATEWAY_IMAGE}"' not in run, "gateway_init_image_mutation_present")
require("gateway_init_image_preservation=PASS" in run, "gateway_init_image_runtime_guard_missing")
require("service_health_proxy_bypass=PASS" in run, "rollout_proxy_bypass_marker_missing")
require("wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config" in run, "rollout_frontend_loopback_proxy_bypass_missing")
require('paths = {getattr(route, "path", None) for route in app.routes}' in run, "websocket_safe_route_audit_missing")
require("route.methods" not in run, "unsafe_route_methods_access_present")
require("post_backup_activity_race_recheck=PASS" in run, "post_backup_activity_race_recheck_missing")
require("pre_provisioner_activity_race_recheck=PASS" in run, "pre_provisioner_activity_race_recheck_missing")
require("pre_gateway_activity_race_recheck=PASS" in run, "pre_gateway_activity_race_recheck_missing")
require(run.count("SELECT count(*) FROM runs WHERE status IN ('pending','running')") >= 4, "active_run_race_rechecks_incomplete")
require("file_agent_config_backup_integrity=PASS" in run, "file_config_backup_integrity_check_missing")
require("empty_agents_backup" in run, "agents_backup_nonempty_check_missing")
require("on_signal" in run and "automatic_rollback \"signal=" in run, "signal_rollback_contract_missing")
require("expected=db" in run and "expected=postgres" in run, "exact_production_backend_guard_missing")
require("provisioner_production_environment=PASS" in run, "production_provisioner_environment_guard_missing")
require("provisioner_skillhub_pvc_runtime_probe=PASS" in run, "three_node_provisioner_runtime_probe_missing")
require('set image deployment/provisioner' in run, "provisioner_image_rollout_missing")
require("provisioner_skillhub_pvc_runtime=PASS" in run, "live_provisioner_runtime_check_missing")
require("skillhub_custom_skill_sandbox_execution_canary=PASS" in run, "managed_sandbox_skill_execution_canary_missing")
require("skillhub_custom_skill_script_execution=PASS" in run, "custom_skill_script_execution_marker_missing")
require("research_fund_settlement_skill_execution=PASS" in run, "research_skill_script_execution_marker_missing")
require("skillhub_custom_skill_pod_identity=PASS" in run, "managed_sandbox_identity_probe_missing")
require("skillhub_custom_skill_gateway_discovery=PASS" in run, "gateway_custom_skill_discovery_probe_missing")
sandbox_api_source = read("sandbox_api_exec.py")
require("sandbox_api_exec.py" in run and "/v1/bash/exec" in sandbox_api_source, "managed_sandbox_api_execution_missing")
require('"Authorization": "Bearer " + api_key' in sandbox_api_source, "managed_sandbox_bearer_auth_missing")
require("X-API-Key" not in sandbox_api_source, "unsupported_managed_sandbox_auth_header_present")
require("existing_sandbox_mutation=NONE" in run, "existing_sandbox_non_mutation_marker_missing")
require("patch deployment provisioner" not in run, "provisioner_environment_patch_forbidden")
frontend_position = run.find("set image deployment/frontend")
require(provisioner_position < canary_position < gateway_position < frontend_position, "provisioner_canary_gateway_frontend_order_invalid")

rollback = read("rollback-digital-employee-domains-datax-01.sh")
skill_restore_position = rollback.find('--restore "${EVIDENCE_DIR}"')
gateway_restore_position = rollback.find("set image deployment/gateway")
require(0 <= skill_restore_position < gateway_restore_position, "skill_restore_must_precede_gateway_restore")
require("ROLLBACK.SHA256" in rollback, "rollback_evidence_verification_missing")
require('"seed-extensions-config=${OLD_GATEWAY_INIT_IMAGE}"' not in rollback, "gateway_init_image_rollback_mutation_present")
require("gateway_init_image=PRESERVED" in rollback, "gateway_init_image_rollback_guard_missing")
require('set image deployment/provisioner' in rollback, "provisioner_image_rollback_missing")
require("provisioner_image=RESTORED" in rollback, "provisioner_rollback_marker_missing")

status = read("status-digital-employee-domains-datax-01.sh")
require("from deerflow.persistence.agents.sql import SqlAgentStore" in status, "status_sql_store_class_probe_missing")
require("SQLAgentStore" not in status, "invalid_status_sql_agent_store_class_name_present")
require("research_fund_settlement_skill_runtime=CHECK_BUILD_INSPECT_PASS" in status, "status_skill_runtime_check_missing")
require("unmigrated_database_agent_categories" in status, "status_database_migration_check_missing")
require("gateway_business_domain_runtime=PASS" in status, "status_gateway_contract_missing")
require("gateway_init_image_preservation=PASS" in status, "status_gateway_init_preservation_missing")
require("service_health_proxy_bypass=PASS" in status, "status_proxy_bypass_marker_missing")
require("wget -Y off -qO- -T 10 http://127.0.0.1:3000/api/runtime-config" in status, "status_frontend_loopback_proxy_bypass_missing")
require("provisioner_skillhub_pvc_runtime=PASS" in status, "status_provisioner_runtime_check_missing")
require("gateway_skill_install_integrity_runtime=PASS" in status, "status_gateway_skill_integrity_check_missing")
require("skillhub_custom_skill_sandbox_identity_contract=PASS" in status, "status_sandbox_identity_contract_missing")

operational = "\n".join(
    read(relative)
    for relative in (
        "run-digital-employee-domains-datax-01.sh",
        "rollback-digital-employee-domains-datax-01.sh",
        "manage-research-fund-settlement-skill-datax-01.sh",
    )
)
require("weknora" not in operational.lower(), "weknora_mutation_present")

print("digital_employee_domains_skill_runtime_r14_static_contracts=PASS")
print("raw_agent_category_patch_contract=PASS backends=file,sql")
print("backend_independent_database_category_normalization=PASS")
print("fail_closed_file_category_normalization=PASS")
print("file_agent_atomic_replace_preflight_contract=PASS")
print("target_image_read_only_file_config_preflight_contract=PASS")
print("target_image_userdata_subpath_contract=PASS subPath=deer-flow")
print("multi_phase_active_work_race_recheck_contract=PASS")
print("signal_rollback_contract=PASS")
print("exact_production_backend_guard=PASS agent_storage=db database=postgres")
print("buildctl_capability_probe_pipefail_sigpipe_guard=PASS")
print("gateway_init_image_preservation_contract=PASS")
print("service_health_proxy_bypass_contract=PASS")
print("websocket_route_runtime_audit_compatibility=PASS")
print("skillhub_complete_tree_install_contract=PASS")
print("provisioner_user_custom_skill_pvc_contract=PASS")
print("provisioner_skill_mount_fail_closed_init_contract=PASS")
print("managed_sandbox_custom_skill_execution_canary_contract=PASS")
print("managed_sandbox_identity_reuse_guard_contract=PASS")
print("research_fund_settlement_xlsx_security_contract=PASS")
print("corporate_business_domains=26 unified=17 distributed=9")
print("admin_category_management=REMOVED")
print("legacy_category_migration_postcheck=PASS")
print("research_fund_settlement_skill_payload=PASS files=%d" % len(actual_skill_files))
print("shared_skill_atomic_backup_install_restore_contract=PASS")
print("combined_automatic_rollback_contract=PASS")
print("rollout_active_work_gate=ZERO_REQUIRED")
print("production_database_schema_mutation_code=NONE")
print("production_database_data_mutation_scope=agents.config.category_id")
print("production_skill_mutation_scope=public/research-fund-settlement")
print("weknora_mutation_code=NONE")
print("provisioner_mutation_scope=image_only_environment_preserved")
print("existing_sandbox_mutation_code=NONE")
