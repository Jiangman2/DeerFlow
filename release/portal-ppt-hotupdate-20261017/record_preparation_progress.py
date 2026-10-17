"""Refresh continuation indexes; immutable evidence and candidate files stay intact."""
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MERGE = ROOT.parent.parent.parent / "portal-ppt-merge-20261017"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def write(path, data):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def main():
    assert (MERGE / "MERGE-STATE.json").is_file(), str(MERGE)
    frontend_result = MERGE / "frontend-build-r1/BUILD-RESULT.json"
    if (MERGE / "frontend-build-r2/BUILD-RESULT.json").is_file():
        frontend_result = MERGE / "frontend-build-r2/BUILD-RESULT.json"
    frontend_status = read(frontend_result)["status"] if frontend_result.is_file() else "LOCAL_BUILD_IN_PROGRESS"
    frontend_qualification = MERGE / "frontend-build-r1/QUALIFICATION-RESULT.json"
    if (MERGE / "frontend-qualification-r2/QUALIFICATION-RESULT.json").is_file():
        frontend_qualification = MERGE / "frontend-qualification-r2/QUALIFICATION-RESULT.json"
    if (MERGE / "frontend-qualification-r3/QUALIFICATION-RESULT.json").is_file():
        frontend_qualification = MERGE / "frontend-qualification-r3/QUALIFICATION-RESULT.json"
    if frontend_qualification.is_file():
        frontend_status = read(frontend_qualification)["status"]
    host_result = MERGE / "host-source-r1/HOST-SOURCE-QUALIFICATION.json"
    host_status = read(host_result).get("status", "RECEIPT_AVAILABLE_REVIEW_REQUIRED") if host_result.is_file() else "SOURCE_PREPARED_OFFLINE_QUALIFICATION_IN_PROGRESS"
    result = {
        "schema": "portal-ppt-current-preparation-progress.v1",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gateway": {
            "status": "MANIFEST_CONFIG_RUNNING_BASELINE_MATCH_EXPORT_PENDING_FILE_TRANSFER",
            "identity": ref(ROOT / "GATEWAY-BASE-IDENTITY-20261017.json"),
            "delivery": ref(ROOT / "DELIVERY-gateway-base-r1.json"),
            "operator_invocation": "Python reported script path absent on worker; exporter did not start",
            "initial_path_error_evidence": ref(ROOT / "OPERATOR-GATEWAY-EXPORT-INVOCATION-20261017-r1.json"),
            "next": "Transfer fixed script from Windows to ecs-zdz-pro-0020 /home/cnooc, then execute once"
        },
        "frontend": {
            "status": frontend_status,
            "input_resolution": ref(MERGE / "FRONTEND-INPUT-RESOLUTION.json"),
            "frozen_inputs": ref(MERGE / "frontend-inputs-r2/INPUTS.json"),
            "build_preparation": ref(MERGE / "frontend-build-r1/PREPARATION.json"),
            "native_arm64_execution": False
        },
        "agent_host": {
            "status": host_status,
            "authority": ref(ROOT / "PPT-CONFIRMATION-SCOPE-AUTHORITY.json"),
            "product_delta_files": 12,
            "fixture_failures_preserved": True,
            "existing_candidate_and_production_unchanged": True
        },
        "prior_build_preparation": ref(ROOT / "BUILD-PREPARATION-RECONCILIATION.json"),
        "assistant_remote_production_connection": False,
        "operator_remote_read_only_metadata_received": True,
        "production_service_mutation": False,
        "production_database_mutation": False,
        "ready_for_complete_release_build": False,
        "ready_for_rollout": False
    }
    if frontend_result.is_file():
        result["frontend"]["build_result"] = ref(frontend_result)
    if frontend_qualification.is_file():
        result["frontend"]["qualification"] = ref(frontend_qualification)
    projected_diagnostic = MERGE / "frontend-qualification-r2/PROJECTED-DIAGNOSTIC.json"
    if projected_diagnostic.is_file():
        result["frontend"]["projected_directory_diagnostic"] = ref(projected_diagnostic)
        result["frontend"]["next"] = "Qualify minimal successor fixing false parent-directory rejection of valid ..timestamp projected-volume paths"
    frontend_final = MERGE / "frontend-qualification-r3/FINAL-REPORT.json"
    frontend_reconciliation = ROOT / "FRONTEND-FINAL-RECONCILIATION.json"
    if frontend_final.is_file() and frontend_reconciliation.is_file():
        frontend_status = read(frontend_final)["status"]
        result["frontend"].update(
            status=frontend_status,
            frozen_inputs=ref(MERGE / "frontend-inputs-r3/INPUTS.json"),
            build_preparation=ref(MERGE / "frontend-build-r2/PREPARATION.json"),
            final_report=ref(frontend_final), independent_reconciliation=ref(frontend_reconciliation),
            source_patch=ref(MERGE / "frontend-projected-fix-r1/PATCH-PREPARATION.json"),
            next="Use this exact verified OCI for native ARM64 and enterprise integration acceptance")
    if host_result.is_file():
        result["agent_host"]["qualification"] = ref(host_result)
    host_review = MERGE / "host-source-r1/INDEPENDENT-HOST-REVIEW.json"
    if host_review.is_file():
        result["agent_host"]["independent_review"] = ref(host_review)
    host_reconciliation = ROOT / "HOST-QUALIFICATION-RECONCILIATION.json"
    if host_reconciliation.is_file():
        result["agent_host"]["independent_receipt_reconciliation"] = ref(host_reconciliation)
    host_delivery = ROOT / "host-base-verify-r1/INDEPENDENT-REVIEW-DELIVERY.json"
    if host_delivery.is_file():
        result["agent_host"].update(
            physical_base_diagnostic_delivery=ref(host_delivery),
            next="Operator runs the fixed datax-01 read-only diagnostic and returns its safe REPORT.json")
    host_operator = ROOT / "OPERATOR-HOST-BASE-SUMMARY-20261017-r1.json"
    if host_operator.is_file():
        result["agent_host"].update(
            status="SERVER_DIAGNOSTIC_COMPLETE_REPORT_HANDOFF_FAILED_PENDING_REPORT_REVIEW",
            operator_terminal_summary=ref(host_operator),
            report_recovery_command=ref(ROOT / "HOST-REPORT-RECOVERY-COMMAND.txt"),
            full_report_reviewed=False,
            next="Recover the existing fixed-hash report only; do not rerun the 128-layer diagnostic or change home directory ownership")
    host_recovery = ROOT / "OPERATOR-HOST-REPORT-RECOVERY-20261017-r1.json"
    if host_recovery.is_file():
        result["agent_host"].update(
            status="SERVER_DIAGNOSTIC_REPORT_HANDOFF_RECOVERED_LOCAL_COPY_PENDING",
            report_handoff_recovery=ref(host_recovery),
            next="Download the already verified small report and review it; no repeated scan or full base download")
    host_physical_review = ROOT / "host-base-evidence-r1/INDEPENDENT-REVIEW.json"
    if host_physical_review.is_file():
        result["agent_host"].update(
            status="EXACT_PHYSICAL_BASE_AND_SOURCE_PRECONDITIONS_ACCEPTED_OFFLINE_APPEND_PENDING",
            physical_base_review=ref(host_physical_review),
            full_report_reviewed=True,
            runtime_dependencies_fully_qualified=False,
            next="Deliver fixed report-bound 12-file offline append; preserve declared runtime gaps for native qualification")
    runner_diagnostic = MERGE / "runner-build-r1/SOURCE-DIAGNOSTIC-82df7efa1cbc43aaa3ef55b972da182e.json"
    if runner_diagnostic.is_file():
        result["runner"] = {
            "status": "ALL_17_FORWARD_AND_4_ROLLBACK_PHYSICAL_PRECONDITIONS_MATCH_TOOL_REPAIR_PENDING",
            "diagnostic": ref(runner_diagnostic),
            "blocker": "Existing empty versions/.gitkeep rejected by r1 auditor and apply helper; narrow r2 repair pending",
            "original_failure_preserved": True,
            "product_payload_changed": False,
        }
    gateway_export = ROOT / "GATEWAY-EXPORT-RECONCILIATION.json"
    if gateway_export.is_file():
        result["gateway"].update(
            status="REMOTE_FIXED_ARCHIVE_VERIFIED_LOCAL_COPY_PENDING",
            operator_invocation="Exporter completed all five operations; private file handoff succeeded",
            export_reconciliation=ref(gateway_export),
            next="Copy exact private archive to local build input, then independently verify full archive and source preconditions")
    gateway_visibility = ROOT / "GATEWAY-LOCAL-PATH-VISIBILITY-20261017.json"
    if gateway_visibility.is_file():
        result["gateway"]["local_path_visibility"] = ref(gateway_visibility)
    gateway_local_copy = ROOT / "GATEWAY-LOCAL-COPY-20261017.json"
    if gateway_local_copy.is_file():
        result["gateway"].update(
            status="LOCAL_ARCHIVE_SHA256_AND_SIZE_MATCH_FULL_LAYER_AUDIT_PENDING",
            local_copy=ref(gateway_local_copy),
            next="Complete full layer/diffID and actual source precondition audit before creating bound contexts")
    gateway_first_audit = MERGE / "gateway-build-r1/BASE-VERIFICATION-69ae8f02d9ae472192aa3eb891800d59.json"
    if gateway_first_audit.is_file():
        result["gateway"].update(
            status="ALL_29_DIFFIDS_MATCH_SOURCE_AUDIT_REQUIRES_NARROW_TOOL_REPAIR",
            first_local_audit=ref(gateway_first_audit),
            next="Review r2 placeholder handling, then independently verify actual source preconditions")
    guard_review = MERGE / "backend-guards-r2/INDEPENDENT-REVIEW.json"
    for component, filename, status in (
        ("gateway", "BASE-VERIFICATION-b2c1fecd51cf43c2b0832e31b5e2cecd.json", "VERIFIED_29_LAYERS_AND_32_1_PHYSICAL_PRECONDITIONS"),
        ("runner", "BASE-VERIFICATION-9711b46518024a61bdfc11c732bb7e9a.json", "VERIFIED_365_LAYERS_AND_17_4_PHYSICAL_PRECONDITIONS"),
    ):
        audit = MERGE / "backend-guards-r2" / filename
        if audit.is_file() and guard_review.is_file():
            verified = read(audit)
            assert not verified["errors"] and verified["all_source_preconditions_match"]
            result.setdefault(component, {}).update(
                status=status, successful_base_audit=ref(audit), guard_review=ref(guard_review),
                next="Prepare exact local OCI bindings and review bounded candidate/rollback builds")
            result[component].pop("blocker", None)
    backend_preparation = MERGE / "backend-bound-build-r1/PREPARATION.json"
    if backend_preparation.is_file():
        result["backend_build"] = {
            "status": "FOUR_CONTEXTS_BOUND_TO_VERIFIED_BASES_BUILD_TOOL_CLI_FIX_IN_PROGRESS",
            "preparation": ref(backend_preparation),
            "original_preflight_failure": "Installed buildx inspect does not support --format; build did not start",
            "next": "Review narrow CLI-compatible successor, build four exact images, then run disposable PostgreSQL image qualification",
        }
        backend_approval = MERGE / "backend-bound-build-r1/ROOT-BUILD-R2-APPROVAL.json"
        if backend_approval.is_file():
            result["backend_build"].update(
                status="CLI_COMPATIBILITY_FIX_REVIEWED_SINGLE_BUILD_ATTEMPT_STARTED",
                execution_review=ref(backend_approval),
                next="Complete four local OCI exports and run disposable PostgreSQL actual-image qualification")
        backend_started = MERGE / "backend-bound-build-r1/STARTED.json"
        if backend_started.is_file():
            result["backend_build"]["started"] = ref(backend_started)
        backend_result = MERGE / "backend-bound-build-r1/BUILD-RESULT.json"
        if backend_result.is_file():
            result["backend_build"].update(status=read(backend_result)["status"], result=ref(backend_result))
        remainder = MERGE / "backend-bound-build-r1/remainder-r3"
        if (remainder / "ROOT-EXECUTION-APPROVAL.json").is_file():
            result["backend_build"].update(
                status="GATEWAY_BUILT_REMAINING_THREE_CONTINUATION_IN_PROGRESS",
                remainder_review=ref(remainder / "ROOT-EXECUTION-APPROVAL.json"),
                retained_failure_reason="A different task's temporary container violated the frozen no-swap parallel-container rule; own builder stopped and successful Gateway preserved")
        aggregate = remainder / "AGGREGATE-4-BUILD-RESULT.json"
        if aggregate.is_file():
            result["backend_build"].update(
                status=read(aggregate)["status"], aggregate=ref(aggregate),
                next="Qualify migration and compatible rollback using the four actual built images with disposable PostgreSQL")
            for component in ("gateway", "runner"):
                result[component].update(
                    status="FORWARD_AND_COMPATIBLE_ROLLBACK_OCIS_BUILT_PENDING_RUNTIME_ACCEPTANCE",
                    next="Complete actual-image database qualification and native ARM64 acceptance")
        image_tool_review = MERGE / "backend-image-qualification-r1/ROOT-TOOL-REVIEW.json"
        if image_tool_review.is_file():
            result["backend_build"]["image_qualification_tool_review"] = ref(image_tool_review)
        image_execution = MERGE / "backend-image-qualification-r1/ROOT-EXECUTION-BINDING.json"
        if image_execution.is_file():
            result["backend_build"]["image_qualification_execution_binding"] = ref(image_execution)
        image_first_result = MERGE / "backend-image-qualification-r1/EXECUTION-RESULT.json"
        if image_first_result.is_file():
            result["backend_build"]["image_qualification_first_attempt"] = ref(image_first_result)
        image_attempts = []
        for image_attempt in ("backend-image-qualification-r1", "backend-image-qualification-r2", "backend-image-qualification-r3", "backend-image-qualification-r4"):
            image_qualification = MERGE / image_attempt / "RUN.json"
            if image_qualification.is_file():
                image_attempts.append({"attempt": image_attempt, "result": ref(image_qualification)})
                result["backend_build"].update(
                    image_qualification_status=read(image_qualification)["status"], actual_image_qualification=ref(image_qualification))
            elif (MERGE / image_attempt / "STARTED.json").is_file():
                result["backend_build"].update(
                    image_qualification_status="ACTUAL_IMAGE_DATABASE_QUALIFICATION_IN_PROGRESS",
                    current_image_qualification_started=ref(MERGE / image_attempt / "STARTED.json"))
        result["backend_build"]["image_qualification_attempts"] = image_attempts
        successful_rows = []
        for folder, directory in (("gateway", MERGE / "backend-bound-build-r1"),
                                  ("gateway-rollback-compatible", remainder),
                                  ("runner", remainder), ("runner-rollback-compatible", remainder)):
            row = directory / (folder + ".BUILD-RESULT.json")
            if row.is_file() and read(row)["status"] == "BUILT_OCI_VERIFIED_PENDING_RUNTIME_ACCEPTANCE":
                successful_rows.append({"component": folder, "receipt": ref(row)})
        result["backend_build"]["successful_component_rows"] = successful_rows
        image_parent_review = ROOT / "ACTUAL-IMAGE-QUALIFICATION-RECONCILIATION.json"
        if image_parent_review.is_file():
            result["backend_build"].update(
                actual_image_parent_review=ref(image_parent_review),
                next="Qualify native ARM64 service startup and isolated integration; local five-scenario database test is complete")
    host_append = MERGE / "host-oci-overlay-r1/BINDING.json"
    if host_append.is_file():
        result["agent_host"].update(
            offline_append_binding=ref(host_append),
            base_download_required=False,
            candidate_preparation_method="Append exact 12-file payload to the retained server OCI after physical report binding; no dependency installation or image execution")
        for key, filename in (("offline_append_preparation", "PREPARATION.json"),
                              ("offline_append_review", "INDEPENDENT-REVIEW.json")):
            item = host_append.parent / filename
            if item.is_file():
                result["agent_host"][key] = ref(item)
    host_bound_delivery = MERGE / "host-oci-overlay-bound-r1/INDEPENDENT-REVIEW-DELIVERY.json"
    if host_bound_delivery.is_file():
        result["agent_host"].update(
            status="OFFLINE_HOST_CANDIDATE_BUILD_DELIVERED_SERVER_EXECUTION_PENDING",
            bound_append_delivery=ref(host_bound_delivery),
            next="Await the already delivered offline Host build result and verify its report; native runtime qualification remains pending")
    host_append_operator = ROOT / "OPERATOR-HOST-APPEND-SUMMARY-20261017-r1.json"
    if host_append_operator.is_file():
        result["agent_host"].update(
            status="SERVER_HOST_CANDIDATE_BUILT_REPORT_TRANSFER_PENDING",
            candidate_operator_terminal_summary=ref(host_append_operator),
            next="Retrieve and reconcile the fixed small candidate REPORT.json; leave the OCI on datax-01")
    host_candidate_review = ROOT / "host-candidate-evidence-r1/INDEPENDENT-REVIEW.json"
    if host_candidate_review.is_file():
        result["agent_host"].update(
            status=read(host_candidate_review)["status"], candidate_report_review=ref(host_candidate_review),
            next="Qualify the exact Host candidate on native ARM64 in isolation")
    candidate_inventory = MERGE / "CANDIDATE-IMAGE-INVENTORY.json"
    if candidate_inventory.is_file():
        result["candidate_image_inventory"] = ref(candidate_inventory)
    native_delivery = ROOT / "native-preflight-r1/INDEPENDENT-REVIEW-DELIVERY.json"
    if native_delivery.is_file():
        result["native_qualification"] = {
            "status": "READ_ONLY_NODE_PREFLIGHT_DELIVERED_OPERATOR_RESULT_PENDING",
            "preflight_delivery": ref(native_delivery),
            "host_runtime_preparation": ref(MERGE / "native-runtime-preparation-r1/PREPARATION.json"),
            "database_fixture_design": ref(MERGE / "native-db-design-r1/PREPARATION.json"),
            "server_runtime_executed": False,
            "next": "Review current node/workload/PostgreSQL report, then bind exact native fixture and image preloading"
        }
        native_operator = ROOT / "OPERATOR-NATIVE-PREFLIGHT-SUMMARY-20261017-r1.json"
        if native_operator.is_file():
            result["native_qualification"].update(
                status="SERVER_READ_ONLY_PREFLIGHT_COMPLETE_STABLE_FULL_REPORT_PENDING",
                operator_terminal_summary=ref(native_operator),
                next="Receive and verify the existing 70946-byte report with SHA256 96d971e837140ec1cd019d1f6e2b833b17a67c6f8066628c7510fbe50de5bafc; no collection rerun")
        native_review = ROOT / "native-preflight-evidence-r1/REVIEW.json"
        if native_review.is_file():
            result["native_qualification"].update(
                status="CURRENT_BASELINE_RECONCILED_EXACT_IMAGE_PRELOAD_AND_NATIVE_FIXTURES_PENDING",
                full_report_review=ref(native_review),
                full_report_received=True,
                full_report_reviewed=True,
                static_capacity_pass=False,
                resource_recollection_required=False,
                execution_admission="Default scheduler; target-node affinity; requests equal limits; preemptionPolicy Never; bounded deadline",
                next="Preload the six existing exact candidates on datax-02, then run serial isolated native fixtures")
        native_adapters = ROOT / "NATIVE-RUNTIME-PREPARATION-REVIEW.json"
        if native_adapters.is_file():
            result["native_qualification"].update(
                adapter_parent_review=ref(native_adapters),
                host_frontend_preparation=ref(MERGE / "native-host-frontend-r1/PREPARATION.json"),
                database_fixture_design=ref(MERGE / "native-db-design-r2/PREPARATION.json"))
        native_preload_delivery = ROOT / "native-preload-r1/UPLOAD-DELIVERY.json"
        native_preload_review = ROOT / "native-preload-r1/ROOT-DELIVERY-BINDING.json"
        if native_preload_delivery.is_file() and native_preload_review.is_file():
            result["native_qualification"].update(
                status="EXACT_IMAGE_UPLOAD_AND_PRELOAD_DELIVERED_OPERATOR_EXECUTION_PENDING",
                preload_delivery=ref(native_preload_delivery),
                preload_parent_review=ref(native_preload_review),
                preload_executed=False,
                server_runtime_executed=False,
                next="Operator uploads five existing local archives and runs the fixed datax-01 preload command; reconcile its six-image result before native fixture execution")
        offline_review = ROOT / "offline-delivery-r1/ROOT-DELIVERY-REVIEW.json"
        if offline_review.is_file():
            result["native_qualification"].update(
                status="OFFLINE_BUNDLE_DELIVERED_MANUAL_COPY_AND_SERVER_PRELOAD_PENDING",
                offline_delivery=ref(ROOT / "offline-delivery-r1/DELIVERY.json"),
                offline_parent_review=ref(offline_review),
                direct_upload_failure=ref(ROOT / "OPERATOR-UPLOAD-FAILURE-20261017-r1.json"),
                local_to_server_network_required=False,
                manual_file_copy_required=True,
                next="Operator manually copies the fixed offline tar and shell launcher to datax-01 /home/cnooc and runs bash; reconcile the six-image preload result before native fixture execution")
        source_failure = ROOT / "native-preload-attempt-r1/REVIEW.json"
        if source_failure.is_file():
            result["native_qualification"].update(
                status="OFFLINE_INSTALL_VERIFIED_SOURCE_STORAGE_CONTINUATION_IN_PREPARATION",
                first_preload_attempt_review=ref(source_failure),
                source_storage_evidence=ref(ROOT / "native-preload-attempt-r1/STORAGE-EVIDENCE.json"),
                offline_copy_and_unpack_verified=True,
                archives_reupload_required=False,
                target_transfer_started=False,
                target_import_started=False,
                next="Deliver a small fixed successor using the verified /opt filesystem for source sealed copies; retain all existing archives and failure evidence")
        source_recovery_delivery = ROOT / "native-preload-r2/DELIVERY.json"
        if source_recovery_delivery.is_file():
            result["native_qualification"].update(
                status="SOURCE_STORAGE_RECOVERY_SCRIPT_DELIVERED_OPERATOR_EXECUTION_PENDING",
                source_recovery_delivery=ref(source_recovery_delivery),
                source_recovery_parent_review=ref(ROOT / "native-preload-r2/ROOT-DELIVERY-REVIEW.json"),
                selected_source_seal_filesystem="/opt",
                offline_copy_and_unpack_verified=True,
                archives_reupload_required=False,
                next="Operator copies only the r2 Python continuation and runs it on datax-01; existing archives and original failure evidence remain intact")
        target_storage_review = ROOT / "native-preload-attempt-r2/TARGET-STORAGE-REVIEW.json"
        if target_storage_review.is_file():
            result["native_qualification"].update(
                status="SOURCE_SEAL_COMPLETE_TARGET_STORAGE_RECOVERY_IN_PREPARATION",
                second_preload_attempt_review=ref(ROOT / "native-preload-attempt-r2/REVIEW.json"),
                target_storage_failure_review=ref(target_storage_review),
                source_seal_completed=True,
                source_sealed_directory="/opt/portal-ppt-native-sealed-r2-e8104bf11adc4959ad0b6078e7c97990",
                target_transfer_started=False,
                target_import_started=False,
                archives_reupload_required=False,
                next="Reuse the six completed source seals; inspect exact target cache and staging ownership to calculate an incremental storage plan without assuming cache reuse")
        cache_diagnostic_delivery = ROOT / "native-cache-diagnostic-r1/DELIVERY.json"
        if cache_diagnostic_delivery.is_file():
            result["native_qualification"].update(
                status="READ_ONLY_TARGET_CACHE_DIAGNOSTIC_DELIVERED_OPERATOR_RESULT_PENDING",
                target_cache_diagnostic_delivery=ref(cache_diagnostic_delivery),
                target_cache_diagnostic_review=ref(ROOT / "native-cache-diagnostic-r1/ROOT-DELIVERY-REVIEW.json"),
                next="Operator manually copies the small cache collector to datax-01 and returns its safe summary/report; no archive transfer or import, source seals retained")
        cache_summary_review = ROOT / "native-cache-evidence-r1/REVIEW.json"
        if cache_summary_review.is_file():
            result["native_qualification"].update(
                status="CACHE_SUMMARY_COMPLETE_FULL_REPORT_AND_PEAK_STORAGE_REVIEW_PENDING",
                target_cache_summary=ref(ROOT / "native-cache-evidence-r1/OPERATOR-SUMMARY.json"),
                target_cache_summary_review=ref(cache_summary_review),
                target_cache_collection_complete=True,
                full_cache_report_received=False,
                next="Receive the existing 392598-byte cache report (SHA256 5a3b7b992a170204247d27a33b740b78d65f0d89c443032020669023c3e7ba15); reconcile exact cache and serial physical storage peak before successor import")
        full_cache_review = ROOT / "native-cache-evidence-r1/FULL-REPORT-REVIEW.json"
        if full_cache_review.is_file():
            result["native_qualification"].update(
                status="FULL_CACHE_RECONCILED_TWO_PHASE_PRELOAD_PREPARATION",
                full_cache_report_received=True,
                full_cache_report=ref(ROOT / "native-cache-evidence-r1/REPORT.json"),
                full_cache_review=ref(full_cache_review),
                two_phase_import_review=ref(ROOT / "native-cache-evidence-r1/TWO-PHASE-IMPORT-REVIEW.json"),
                layer_allocation_review=ref(ROOT / "native-allocation-review-r1/ALLOCATION.json"),
                hardlink_review=ref(ROOT / "native-allocation-review-r1/HARDLINK-REVIEW.json"),
                old_image_archive_authorization="Conditional fallback authorized by user; only after identifying unused non-rollback images and verifying an archive. No deletion performed.",
                next="Qualify the small r3 two-phase preloader; reuse source seals and keep full uploads/seals on target /opt")
        preload_r3 = ROOT / "native-preload-r3/ROOT-DELIVERY-REVIEW.json"
        if preload_r3.is_file():
            result["native_qualification"].update(
                status="TWO_PHASE_PRELOAD_R3_DELIVERED_OPERATOR_EXECUTION_PENDING",
                two_phase_preload_review=ref(preload_r3),
                two_phase_preload_delivery=ref(ROOT / "native-preload-r3/DELIVERY.json"),
                source_seal_completed=True, archives_reupload_required=False,
                target_transfer_started=False, target_import_started=False,
                server_runtime_executed=False,
                next="Manually copy only preload-portal-ppt-native-20261017-r3.py to datax-01 /home/cnooc and execute once with --distribute; return its report before native runtime fixtures")
        preload_r3_attempt = ROOT / "native-preload-attempt-r3/REVIEW.json"
        if preload_r3_attempt.is_file():
            result["native_qualification"].update(
                status="R3_TRANSFER_COMPLETE_REMOTE_RESULT_MISSING_TARGET_LOCAL_CONTINUATION_PREPARATION",
                two_phase_preload_attempt_review=ref(preload_r3_attempt),
                two_phase_preload_source_report=ref(ROOT / "native-preload-attempt-r3/REPORT.json"),
                target_transfer_started=True, target_transfer_completed=True,
                target_import_started=None,
                target_import_state="UNKNOWN_SOURCE_SSH_RESULT_MISSING",
                prepared_target_state="PREPARED_NO_IMPORT_AT_PRIOR_SAMPLE",
                archives_reupload_required=False, server_runtime_executed=False,
                next="Prepare a small direct datax-02 continuation; under the existing lock require exact STARTED/PREPARED only, then reuse the frozen import and preserve original source failure")
        local_resume = ROOT / "native-preload-r3-local-resume/ROOT-DELIVERY-REVIEW.json"
        if local_resume.is_file():
            result["native_qualification"].update(
                status="R3_TARGET_LOCAL_CONTINUATION_DELIVERED_OPERATOR_EXECUTION_PENDING",
                target_local_continuation_review=ref(local_resume),
                target_local_continuation_delivery=ref(ROOT / "native-preload-r3-local-resume/DELIVERY.json"),
                next="Manually copy the small local continuation to datax-02 /home/cnooc; execute once there, return the complete report, and retain all existing archives/evidence")
        local_resume_failure = ROOT / "native-preload-attempt-r3-local-resume/REVIEW-PENDING.json"
        if local_resume_failure.is_file():
            result["native_qualification"].update(
                status="R3_LOCAL_TARGET_IMPORT_REQUESTED_STORAGE_GATE_FAILED_REPORT_PENDING",
                local_target_failure_summary=ref(ROOT / "native-preload-attempt-r3-local-resume/OPERATOR-SUMMARY.json"),
                local_target_failure_review=ref(local_resume_failure),
                target_import_started=None,
                target_import_state="REQUESTED_PARTIAL_IMAGE_IMPORT_POSSIBLE",
                archives_reupload_required=False,
                next="Receive existing target report 10720 bytes SHA3b1c3d86...; identify stopped phase and measured storage deficit before a reviewed checkpoint continuation or unused-image archival")
        local_resume_full_review = ROOT / "native-preload-attempt-r3-local-resume/REVIEW.json"
        if local_resume_full_review.is_file():
            result["native_qualification"].update(
                status="R3_PHASE_A_COMPLETE_PHASE_B_STORAGE_RECOVERY_PREPARATION",
                local_target_failure_report=ref(ROOT / "native-preload-attempt-r3-local-resume/REPORT.json"),
                local_target_failure_review=ref(local_resume_full_review),
                target_import_started=True,
                target_import_state="SIX_PHASE_A_CONTENT_IMPORTS_PASSED_PHASE_B_NOT_STARTED",
                phase_a="PASS", phase_b="NOT_REQUESTED",
                last_storage_budget_deficit_bytes=206887673,
                phase_a_replay_required=False, archives_reupload_required=False,
                next="Collect bounded read-only target image/checkpoint inventory; identify unused non-rollback archive candidates and prepare a phase-B-only continuation without lowering reserves")
        phase_b_preparation = ROOT / "native-phase-b-resume-r1/QUALIFICATION.json"
        if phase_b_preparation.is_file():
            result["native_qualification"].update(
                phase_b_continuation_preparation=dict(status="OFFLINE_GUARD_QUALIFIED_UNBOUND_NOT_DELIVERED",
                    qualification=ref(phase_b_preparation),
                    script=ref(ROOT / "native-phase-b-resume-r1/resume_phase_b.py"),
                    required_checkpoint_pins=24, ready_for_execution=False))
        archive_inventory = ROOT / "native-image-archive-inventory-r1/ROOT-DELIVERY-REVIEW.json"
        if archive_inventory.is_file():
            result["native_qualification"].update(
                status="PHASE_A_COMPLETE_READ_ONLY_TARGET_INVENTORY_DELIVERED",
                archive_inventory_review=ref(archive_inventory),
                archive_inventory_delivery=ref(ROOT / "native-image-archive-inventory-r1/DELIVERY.json"),
                cluster_protected_images=ref(ROOT / "native-image-archive-inventory-r1/CLUSTER-PROTECTED-IMAGES.json"),
                next="Operator manually copies the small read-only inventory collector to datax-02 and returns its safe report; cross-check candidates against the 103 cluster references, runtime use and explicit rollback pins, then recover space and bind phase-B continuation")
        accepted_inventory = ROOT / "native-image-inventory-evidence-r1/REVIEW.json"
        if accepted_inventory.is_file():
            result["native_qualification"].update(
                status="PHASE_A_COMPLETE_EXACT_INVENTORY_REVIEWED_PHASE_B_BINDING_READY",
                accepted_inventory_review=ref(accepted_inventory),
                archive_fallback_review=ref(ROOT / "native-image-archive-inventory-r1/CANDIDATE-REVIEW.json"),
                phase_a="PASS", phase_b="NOT_REQUESTED",
                inventory_root_available_bytes=69127147520,
                margin_over_previous_phase_b_budget_bytes=118912263,
                fresh_execution_budget_pass=False,
                image_archival_performed=False, image_deletion_performed=False,
                next="Bind and deliver phase-B-only continuation; historical free-space snapshot fits the prior budget, but the unchanged live storage/content/reference checks must pass at execution")
        phase_b_delivery = ROOT / "native-phase-b-resume-r1/ROOT-DELIVERY-REVIEW.json"
        if phase_b_delivery.is_file():
            result["native_qualification"].update(
                status="PHASE_B_ONLY_CONTINUATION_DELIVERED_OPERATOR_EXECUTION_PENDING",
                phase_b_delivery=ref(ROOT / "native-phase-b-resume-r1/DELIVERY.json"),
                phase_b_delivery_review=ref(phase_b_delivery),
                phase_b_continuation_preparation=dict(
                    status="BOUND_REVIEWED_DELIVERED_NOT_SERVER_EXECUTED",
                    qualification=ref(phase_b_preparation),
                    bound_delta_qualification=ref(ROOT / "native-phase-b-resume-r1/BOUND-DELTA-QUALIFICATION.json"),
                    independent_review=ref(ROOT / "native-phase-b-resume-r1/INDEPENDENT-REVIEW.json"),
                    script=ref(ROOT / "native-phase-b-resume-r1/resume_phase_b_bound.py"),
                    required_checkpoint_pins=24, ready_for_execution=True,
                    execution_time_conditions_required=True),
                phase_a_replay_required=False, archives_reupload_required=False,
                next="Operator manually copies only the bound phase-B Python script to datax-02 /home/cnooc and runs --resume-phase-b once; review its report before isolated native runtime qualification. No old r3/local-resume rerun, archival, deletion or reserve reduction")
        inventory_supplement = ROOT / "native-image-inventory-evidence-r2/REVIEW.json"
        if inventory_supplement.is_file():
            result["native_qualification"].update(
                supplemental_inventory_review=ref(inventory_supplement),
                phase_b_continuation_execution_state="UNKNOWN_RESULT_NOT_PROVIDED",
                next="REPORT(5) is another read-only inventory, not the delivered phase-B continuation result. Clarify whether the continuation was executed; obtain its existing terminal summary/download_report if yes, or execute the already delivered script once if not. Do not infer successor state from the preserved original checkpoint or request a blind retry")
        phase_b_attempt = ROOT / "native-phase-b-attempt-r1/REVIEW-PENDING.json"
        if phase_b_attempt.is_file():
            result["native_qualification"].update(
                status="PHASE_B_PARTIAL_FRONTEND_COMPLETE_GATEWAY_NONZERO_REPORT_PENDING",
                phase_b="PARTIAL_IMPORT_FAILURE", phase_b_continuation_execution_state="EXECUTED_FAILED",
                phase_b_operator_summary=ref(ROOT / "native-phase-b-attempt-r1/OPERATOR-SUMMARY.json"),
                phase_b_pending_review=ref(phase_b_attempt),
                target_import_state="PHASE_A_COMPLETE_PHASE_B_FRONTEND_CHECKED_GATEWAY_NONZERO",
                next="Read the existing 12001-byte Phase-B failure report with SHA ff707b34f008c642c44127b2b363241426649bb34b42a894d5d83a66b1307f82 at the exact operator download_report path. Preserve partial imports and all once markers; no continuation retry or further imports before failure review")
            result["native_qualification"]["phase_b_continuation_preparation"].update(
                status="BOUND_DELIVERY_EXECUTED_FAILED_DO_NOT_RERUN", ready_for_execution=False)
        phase_b_failure = ROOT / "native-phase-b-attempt-r1/REVIEW.json"
        if phase_b_failure.is_file():
            result["native_qualification"].update(
                status="PHASE_B_PARTIAL_FAILURE_EXACT_REPORT_REVIEWED_DIAGNOSTIC_PENDING",
                phase_b_full_report=ref(ROOT / "native-phase-b-attempt-r1/REPORT.json"),
                phase_b_full_review=ref(phase_b_failure),
                frontend_preload_complete=True,
                gateway_preload_complete=False, gateway_import_state="NONZERO_PARTIAL_STATE_UNKNOWN",
                latest_gateway_precheck_margin_bytes=3749127,
                failing_subprocess_reason="NOT_ESTABLISHED_STDERR_NOT_RETAINED",
                next="Collect read-only fixed failure-window containerd journal and exact Gateway snapshot progress. The original CLI stderr was discarded, so generic import_nonzero does not establish a cause. No retry, deletion or cleanup")
        phase_b_diagnostic = ROOT / "native-phase-b-diagnostic-r1/ROOT-DELIVERY-REVIEW.json"
        if phase_b_diagnostic.is_file():
            result["native_qualification"].update(
                status="PHASE_B_GATEWAY_FAILURE_READ_ONLY_DIAGNOSTIC_DELIVERED",
                phase_b_diagnostic_delivery=ref(ROOT / "native-phase-b-diagnostic-r1/DELIVERY.json"),
                phase_b_diagnostic_root_review=ref(phase_b_diagnostic),
                next="Manually copy diagnose-portal-ppt-phase-b-20261017-r1.py to datax-02 /home/cnooc and run --collect; return its new terminal summary and retain the exact download_report. It only reads fixed failure receipts, bounded containerd journal and Gateway snapshots. Do not rerun the failed import or older inventory collectors")
        diagnostic_evidence = ROOT / "native-phase-b-diagnostic-evidence-r1/REVIEW.json"
        if diagnostic_evidence.is_file():
            result["native_qualification"].update(
                status="GATEWAY_DIAGNOSTIC_REVIEWED_ONE_CAPTURED_ATTEMPT_IN_PREPARATION",
                phase_b_diagnostic_report=ref(ROOT / "native-phase-b-diagnostic-evidence-r1/REPORT.json"),
                phase_b_diagnostic_report_review=ref(diagnostic_evidence),
                gateway_committed_prefix_at_last_sample=0,
                gateway_failure_cause="UNDETERMINED_NO_MATCHED_DAEMON_LOGS",
                next="Prepare a Gateway-only single attempt under unchanged fresh checks, preserving actual subprocess returncode and bounded private stdout/stderr. Verify frontend read-only, retain old markers, and stop after Gateway success or failure. No further inventory-only diagnostic or blind retry")
        gateway_recovery = ROOT / "native-gateway-recovery-r1/ROOT-DELIVERY-REVIEW.json"
        if gateway_recovery.is_file():
            result["native_qualification"].update(
                status="GATEWAY_SINGLE_CAPTURED_ATTEMPT_DELIVERED_OPERATOR_EXECUTION_PENDING",
                gateway_recovery_delivery=ref(ROOT / "native-gateway-recovery-r1/DELIVERY.json"),
                gateway_recovery_root_review=ref(gateway_recovery),
                gateway_recovery_server_executed=False,
                next="Manually copy recover-portal-ppt-gateway-20261017-r1.py to datax-02 /home/cnooc and execute --recover-gateway-once once. Return the new complete terminal summary and preserve its download_report. This freshly checks the original content, reference, snapshot and storage gates; captures the actual Gateway child returncode and bounded private logs; verifies frontend read-only; stops after Gateway. Do not rerun old scripts or remove old markers.")
        gateway_success_pending = ROOT / "native-gateway-recovery-evidence-r1/REVIEW-PENDING.json"
        if gateway_success_pending.is_file():
            result["native_qualification"].update(
                status="GATEWAY_IMPORT_ZERO_SUCCESS_REPORTED_FULL_REPORT_PENDING",
                gateway_recovery_server_executed=True,
                gateway_recovery_operator_summary=ref(ROOT / "native-gateway-recovery-evidence-r1/OPERATOR-SUMMARY.json"),
                gateway_recovery_pending_review=ref(gateway_success_pending),
                gateway_import_returncode_reported=0,
                next="Receive existing 6504-byte Gateway report SHA c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662; verify completion and bind the remaining four image roles. Frontend and Gateway must not be reimported.")
        gateway_success = ROOT / "native-gateway-recovery-evidence-r1/REVIEW.json"
        if gateway_success.is_file():
            result["native_qualification"].update(
                status="FRONTEND_AND_GATEWAY_PRELOAD_COMPLETE_REMAINING_FOUR_PREPARATION",
                gateway_recovery_full_report=ref(ROOT / "native-gateway-recovery-evidence-r1/REPORT.json"),
                gateway_recovery_full_review=ref(gateway_success),
                frontend_preload_complete=True, gateway_preload_complete=True,
                gateway_import_state="CONTENT_UNPACK_AND_CRI_VERIFIED_61_COMMITTED_LAYERS",
                gateway_import_returncode=0, gateway_committed_prefix_at_last_sample=61,
                gateway_import_elapsed_seconds=112.815,
                next="Prepare and review a bound remaining-four-only continuation: compatible rollback Gateway, forward Runner, compatible rollback Runner, and forward Host. Reuse exact sealed metadata, preserve original live gates, capture each child result/log, and stop on any failure. Do not replay frontend or forward Gateway.")
        remaining_delivery = ROOT / "native-remaining-preload-r1/ROOT-DELIVERY-REVIEW.json"
        if remaining_delivery.is_file():
            result["native_qualification"].update(
                status="REMAINING_FOUR_PRELOAD_DELIVERED_OPERATOR_EXECUTION_PENDING",
                remaining_preload_delivery=ref(ROOT / "native-remaining-preload-r1/DELIVERY.json"),
                remaining_preload_root_review=ref(remaining_delivery),
                remaining_preload_server_executed=False,
                next="Manually copy resume-portal-ppt-remaining-20261017-r1.py to datax-02 /home/cnooc and execute --resume-remaining-once once. It handles the four remaining roles with original live gates and retained per-role diagnostics, verifies completed frontend/Gateway read-only, and stops on failure. Return the new terminal summary and preserve download_report before native runtime qualification.")
        remaining_partial = ROOT / "native-remaining-preload-evidence-r1/REVIEW.json"
        if remaining_partial.is_file():
            result["native_qualification"].update(
                status="THREE_IMAGES_COMPLETE_RUNNER_SNAPSHOT_GATE_REVIEW",
                remaining_preload_server_executed=True,
                remaining_preload_partial_report=ref(ROOT / "native-remaining-preload-evidence-r1/REPORT.json"),
                remaining_preload_partial_review=ref(remaining_partial),
                gateway_rollback_preload_complete=True,
                completed_preload_roles=["frontend", "gateway-forward", "gateway-rollback-compatible"],
                runner_forward_import_started=False,
                snapshot_gate_code_issue="ACTIVE_OR_VIEW_PARENT_COLUMN_MATCH_CAN_FALSELY_BLOCK_NORMAL_CHILD_SNAPSHOTS",
                next="Review the read-only Active/View key/parent/kind rows, correct the field distinction and qualify a remaining-three-only continuation. Do not rerun completed image imports or old one-shot scripts.")
        snapshot_cause = ROOT / "native-remaining-preload-evidence-r1/SNAPSHOT-CAUSE-REVIEW.json"
        if snapshot_cause.is_file():
            result["native_qualification"].update(
                status="THREE_IMAGES_COMPLETE_PARENT_COLUMN_FALSE_POSITIVE_REPRODUCED",
                snapshot_cause_review=ref(snapshot_cause),
                snapshot_operator_projection=ref(ROOT / "native-remaining-preload-evidence-r1/OPERATOR-ACTIVE-PROJECTION.json"),
                snapshot_gate_failure_cause="ORDINARY_ACTIVE_CHILD_PARENT_MATCHES_COMMITTED_RUNNER_CHAIN_342_OR_365",
                next="Qualify the corrected KEY/PARENT distinction, bind the exact partial report, and deliver only the three remaining Runner/Host preloads. Preserve all completed images and original live space checks.")
        remaining_three = ROOT / "native-remaining-three-r1/ROOT-DELIVERY-REVIEW.json"
        if remaining_three.is_file():
            result["native_qualification"].update(
                status="CORRECTED_REMAINING_THREE_DELIVERED_OPERATOR_EXECUTION_PENDING",
                remaining_three_delivery=ref(ROOT / "native-remaining-three-r1/DELIVERY.json"),
                remaining_three_root_review=ref(remaining_three), remaining_three_server_executed=False,
                next="Manually copy resume-portal-ppt-remaining-three-20261017-r1.py to datax-02 /home/cnooc; execute --resume-three-once once. Only the two Runner images and forward Host may import. Return its terminal summary and keep its download_report before native runtime qualification.")
        preload_complete = ROOT / "native-preload-complete-evidence-r1/REVIEW.json"
        if preload_complete.is_file():
            result["native_qualification"].update(
                status="ALL_SIX_PRELOAD_COMPLETE_NATIVE_RUNTIME_PACKAGE_PREPARATION",
                final_preload_report=ref(ROOT / "native-preload-complete-evidence-r1/REPORT.json"),
                final_preload_review=ref(preload_complete),
                host_runtime_inputs=ref(ROOT / "native-preload-complete-evidence-r1/HOST-RUNTIME-INPUTS.json"),
                postgres_cache=ref(ROOT / "native-preload-complete-evidence-r1/POSTGRES-CACHE.json"),
                all_six_image_receipts=ref(ROOT / "native-preload-complete-evidence-r1/IMAGE-RECEIPTS.json"),
                remaining_three_server_executed=True, phase_b="PASS",
                target_import_state="ALL_SIX_CONTENT_UNPACK_AND_CRI_VERIFIED_NATIVE_RUNTIME_PENDING",
                completed_preload_roles=["frontend", "gateway-forward", "gateway-rollback-compatible", "ppt-master-runner-forward", "ppt-master-runner-rollback-compatible", "ppt-master-agent-host-forward"],
                runner_forward_import_started=True, all_six_preload_complete=True,
                image_reimport_required=False, fresh_native_node_and_network_proof_required=True,
                next="Bind the existing network, Host, Frontend and backend/DB probes into one isolated native Kubernetes qualification package. Reuse exact preloaded image identities, safe Host runtime inputs and PG16 cache projection; actual UID/admission/network/runtime results remain required before release.")
        native_delivery = ROOT / "native-runtime-qualification-r1/ROOT-DELIVERY-REVIEW.json"
        if native_delivery.is_file():
            result["native_qualification"].update(
                status="ALL_SIX_PRELOADED_NATIVE_QUALIFICATION_DELIVERED_EXECUTION_PENDING",
                native_runtime_delivery=ref(ROOT / "native-runtime-qualification-r1/DELIVERY.json"),
                native_runtime_root_review=ref(native_delivery),
                native_controller_qualification=ref(ROOT / "native-runtime-qualification-r1/QUALIFICATION.json"),
                native_controller_independent_review=ref(ROOT / "native-runtime-qualification-r1/INDEPENDENT-REVIEW.json"),
                native_runtime_server_executed=False, native_runtime_passed=False,
                native_controller_execution_host="datax-01", native_test_target="datax-02",
                image_reimport_required=False, extra_image_transfer_required=False,
                next="Manually copy only qualify-portal-ppt-native-20261017-r1.py to datax-01 /home/cnooc and execute --qualify-native-once once. The controller uses exact cached images on datax-02, proves owned network isolation, then runs Host, Frontend, PG profile and five-step DB compatibility fixtures. Return full terminal summary and its new safe report; preserve failures and once markers. Production workloads/database/model remain untouched; no rollout readiness is claimed.")
        native_attempt = ROOT / "native-runtime-attempt-r1/REVIEW-PENDING.json"
        if native_attempt.is_file():
            result["native_qualification"].update(
                status="NATIVE_NETWORK_PHASE_FAILED_EXACT_REPORT_REVIEW_PENDING",
                native_runtime_server_executed=True, native_runtime_passed=False,
                native_runtime_operator_summary=ref(ROOT / "native-runtime-attempt-r1/OPERATOR-SUMMARY.json"),
                native_runtime_attempt_pending_review=ref(native_attempt),
                last_native_owner="9a948f58627a40ecadd0a5ee1fa2fc7a",
                last_native_error="kubectl_nonzero_1", native_failure_cause="UNDETERMINED_SAFE_REPORT_AND_COMMAND_RECEIPTS_REQUIRED",
                next="Read the existing exact 1771-byte safe report SHA fdd49101a4e8892b92945a30b7334f3f5b7d2436e13f590720f4a69f7f3b2a2c and failed command receipts for owner9a948f58627a40ecadd0a5ee1fa2fc7a on datax-01. Network proof did not pass; Host, Frontend and database stages were not entered. Preserve all once markers, owned resources and private raw logs. Do not rerun native qualification or any preload.")
        native_attempt_review = ROOT / "native-runtime-attempt-r1/REVIEW.json"
        if native_attempt_review.is_file():
            result["native_qualification"].update(
                status="NATIVE_NETWORK_SUBJECT_CREATE_FAILED_EXACT_STDERR_PENDING",
                native_runtime_attempt_report=ref(ROOT / "native-runtime-attempt-r1/REPORT.json"),
                native_runtime_attempt_review=ref(native_attempt_review),
                native_runtime_failed_commands=ref(ROOT / "native-runtime-attempt-r1/FAILED-COMMAND-RECEIPTS.json"),
                native_failure_cause="UNDETERMINED_FIXED_270_BYTE_CREATE_ERROR_REQUIRED",
                production_identity_and_shape_stable=True, retained_namespace_deny_all=True,
                next="Read only the pinned 270-byte command-0015.stderr.private.log for network-subject Pod creation, SHA dfdbf9ce2e5c8104d66ac46a0a92b9abe4e67a4eb381d3c7324d0d8591c5b9ad. The exact failure report is accepted; both additive policies were removed, deny-all retained, production shape unchanged, no Host/Frontend/DB stage entered. Preserve old evidence and do not rerun qualification or preload.")
        native_attempt_cause = ROOT / "native-runtime-attempt-r1/CAUSE-REVIEW.json"
        if native_attempt_cause.is_file():
            result["native_qualification"].update(
                status="NATIVE_PRIORITY_ADMISSION_MISMATCH_VERIFIED_SUCCESSOR_PREPARATION",
                native_runtime_cause_review=ref(native_attempt_cause),
                native_runtime_create_error=ref(ROOT / "native-runtime-attempt-r1/CREATE-ERROR.txt"),
                native_failure_cause="POD_NEVER_WITHOUT_EXPLICIT_NONPREEMPTING_PRIORITY_CLASS_CONFLICTS_WITH_DEFAULT_PREEMPT_LOWER_PRIORITY",
                next="Prepare a new r2 native controller with a unique UID-bound non-globalDefault Never PriorityClass value0 and exact scoped cleanup. Bind original failure report/error as preconditions; preserve r1 source, once marker, namespace and evidence. Qualify focused priority-admission/lifecycle deltas, independently review and deliver one successor file; no image transfer or old script rerun.")
        native_r2_delivery = ROOT / "native-runtime-qualification-r2/ROOT-DELIVERY-REVIEW.json"
        if native_r2_delivery.is_file():
            result["native_qualification"].update(
                status="NATIVE_PRIORITY_CLASS_FIX_R2_DELIVERED_OPERATOR_EXECUTION_PENDING",
                native_runtime_r1_delivery=ref(ROOT / "native-runtime-qualification-r1/DELIVERY.json"),
                native_runtime_delivery=ref(ROOT / "native-runtime-qualification-r2/DELIVERY.json"),
                native_runtime_root_review=ref(native_r2_delivery),
                native_controller_qualification=ref(ROOT / "native-runtime-qualification-r2/QUALIFICATION.json"),
                native_controller_independent_review=ref(ROOT / "native-runtime-qualification-r2/INDEPENDENT-REVIEW.json"),
                previous_native_r1_executed=True, previous_native_r1_failure_reviewed=True,
                native_runtime_server_executed=False, native_runtime_r2_server_executed=False,
                native_runtime_passed=False, old_r1_marker_and_evidence_preserved=True,
                next="Manually copy only qualify-portal-ppt-native-20261017-r2.py to datax-01 /home/cnooc and execute --qualify-native-once once. This reviewed successor binds the exact r1 failure, creates a unique UID-bound non-default Never PriorityClass, verifies dry-run admission, then performs original isolated network/Host/Frontend/PG/DB stages. Preserve old r1 marker, namespace and evidence; no image reimport. Return the new terminal summary and safe report; do not rerun r1 or r2 after failure.")
        native_r2_attempt = ROOT / "native-runtime-attempt-r2/REVIEW-PENDING.json"
        if native_r2_attempt.is_file():
            result["native_qualification"].update(
                status="NATIVE_R2_ADMISSION_PASSED_NETWORK_CONVERGENCE_FAILURE_REVIEW_PENDING",
                native_runtime_server_executed=True, native_runtime_r2_server_executed=True,
                native_runtime_passed=False,
                native_runtime_r2_operator_summary=ref(ROOT / "native-runtime-attempt-r2/OPERATOR-SUMMARY.json"),
                native_runtime_r2_review_pending=ref(native_r2_attempt),
                last_native_owner="6ada1851c83e433aba56bfd91a13da83",
                last_native_error="network_convergence_deadline",
                native_failure_cause="UNDETERMINED_SAVED_NETWORK_PACKET_AND_TIMING_PROJECTIONS_REQUIRED",
                next="Read the existing exact r2 report (2873 bytes, SHA cc10c4d5996ed6d7fa16e9fa70399d0efaded3530e1833b8df1369102eaec5e2) and saved network probe status/timing projections. Priority admission passed. Distinguish CNI isolation results from serialized kubectl overhead before any correction; preserve r1/r2 markers and evidence, no rerun or new network probes.")
        native_r2_review = ROOT / "native-runtime-attempt-r2/REVIEW.json"
        if native_r2_review.is_file():
            result["native_qualification"].update(
                status="NATIVE_R2_OBSERVED_NETWORK_ISOLATION_NOT_ENFORCED_ALTERNATIVE_PREPARATION",
                native_runtime_r2_report=ref(ROOT / "native-runtime-attempt-r2/REPORT.json"),
                native_runtime_r2_review=ref(native_r2_review),
                native_runtime_r2_network_projection=ref(ROOT / "native-runtime-attempt-r2/NETWORK-PROJECTION.json"),
                native_failure_cause="ALL_SIX_POST_WITHDRAWAL_CROSS_CALLS_REMAIN_SUCCESS_WITH_HEALTHY_LISTENERS",
                network_policy_enforcement_proven=False,cluster_wide_policy_component_absence_established=False,
                production_identity_and_shape_stable=True,retained_namespace_deny_all=True,
                next="Do not retry or relax the r2 network proof. The exact safe report and saved-call projection show three post-withdrawal rounds still bidirectionally reachable, 18 healthy samples and no CLI errors. Read network component/policy names only, then prepare a verifiable native isolation method confined to owned fixtures; no production CNI installation/change is authorized by this diagnostic.")
        isolation_strategy = ROOT / "native-runtime-attempt-r2/ISOLATION-STRATEGY.json"
        if isolation_strategy.is_file():
            result["native_qualification"].update(
                status="NATIVE_OWNED_POD_LOOPBACK_GUARD_PREPARATION",
                native_runtime_network_component_projection=ref(ROOT / "native-runtime-attempt-r2/NETWORK-COMPONENT-PROJECTION.json"),
                native_runtime_isolation_strategy=ref(isolation_strategy),
                planned_isolation_mechanism="LINK_DOWN_LOOPBACK_ONLY",
                production_cni_change=False,network_policy_enforcement_proven=False,
                next="Qualify and independently review a new owned-Pod link-guard helper and r3 controller. Close only temporary Pods' non-loopback links using a narrowly scoped first ordinary NET_ADMIN init; business processes keep original UIDs, drop-all capabilities, readonly guard receipts and pre-exec netns/link/capability checks. Preserve actual denial/health/identity and time bounds. Do not install CNI, change PSA, reuse old failed scripts or claim NetworkPolicy enforcement.")
        native_r3_delivery = ROOT / "native-runtime-qualification-r3/ROOT-DELIVERY-REVIEW.json"
        if native_r3_delivery.is_file():
            result["native_qualification"].update(
                status="NATIVE_OWNED_POD_LINK_GUARD_R3_DELIVERED_OPERATOR_EXECUTION_PENDING",
                native_runtime_r2_delivery=ref(ROOT / "native-runtime-qualification-r2/DELIVERY.json"),
                native_runtime_delivery=ref(ROOT / "native-runtime-qualification-r3/DELIVERY.json"),
                native_runtime_root_review=ref(native_r3_delivery),
                native_controller_qualification=ref(ROOT / "native-runtime-qualification-r3/QUALIFICATION.json"),
                native_controller_independent_review=ref(ROOT / "native-runtime-qualification-r3/INDEPENDENT-REVIEW.json"),
                native_link_guard_qualification=ref(ROOT / "native-runtime-link-guard-r1/QUALIFICATION.json"),
                native_link_guard_independent_review=ref(ROOT / "native-runtime-link-guard-r1/INDEPENDENT-REVIEW.json"),
                native_link_guard_local_kernel_qualification=ref(ROOT / "native-runtime-link-guard-r1/KERNEL-QUALIFICATION.json"),
                previous_native_r2_executed=True, previous_native_r2_failure_reviewed=True,
                native_runtime_server_executed=False, native_runtime_r2_server_executed=True,
                native_runtime_r3_server_executed=False, native_runtime_passed=False,
                old_r1_r2_markers_and_evidence_preserved=True,
                isolation_mechanism="LINK_DOWN_LOOPBACK_ONLY", network_policy_enforcement_proven=False,
                production_cni_change=False, podsecurity_configuration_change=False,
                image_reimport_required=False, extra_image_transfer_required=False,
                next="Manually copy only qualify-portal-ppt-native-20261017-r3.py to datax-01 /home/cnooc and execute --qualify-native-once once. It preserves r1/r2 failures, uses NET_ADMIN only inside the exact owned proof subject and first ordinary business-test init, verifies actual links/netns/UID/capabilities, then runs unchanged Host/Frontend/PG/DB fixtures. Actual bidirectional denial/health/identity and time bounds still apply. It does not prove NetworkPolicy enforcement, change production CNI/PSA, reimport images or switch production. Return the new terminal summary and safe report; preserve failure evidence and do not rerun.")
        native_r3_attempt = ROOT / "native-runtime-attempt-r3/REVIEW-PENDING.json"
        if native_r3_attempt.is_file():
            result["native_qualification"].update(
                status="NATIVE_R3_FOUR_NETWORK_ROUNDS_REPORTED_VALIDATOR_FAILURE_REVIEW_PENDING",
                native_runtime_server_executed=True, native_runtime_r3_server_executed=True,
                native_runtime_passed=False,
                native_runtime_r3_operator_summary=ref(ROOT / "native-runtime-attempt-r3/OPERATOR-SUMMARY.json"),
                native_runtime_r3_review_pending=ref(native_r3_attempt),
                last_native_owner="5beb351dc97b4fa093ca3a0df477875e", last_native_error="controller_Refused",
                native_failure_cause="UNDETERMINED_FROZEN_VALIDATOR_REFUSED_DETAIL_REDUCED_BY_EXCEPTION_CLASS_BOUNDARY",
                network_isolation_passed=False, r3_cleanup_and_production_stability_review_pending=True,
                next="Read only the fixed existing r3 report (2902 bytes, SHA a354743903cf39c52f6f583d032638b6a80fb0e7a0a18ed10f80e2fde4f0b97f) and saved four-round completion/status/timing projections. Four round progress messages alone do not prove isolation or final validator success. Preserve r1/r2/r3 markers and evidence; no qualifier rerun, new network calls, image operations or CNI/PSA changes.")
    write(ROOT / "CURRENT-PREPARATION-PROGRESS.json", result)
    pending = []
    if not (ROOT / "ACTUAL-IMAGE-QUALIFICATION-RECONCILIATION.json").is_file():
        pending.append("Complete database compatibility qualification using the four built Gateway and Runner candidate/compatible rollback images")
    if not host_candidate_review.is_file():
        pending.append("Reconcile the Host offline candidate build report; operator reports completed archive verification and handoff")
    if not (ROOT / "native-preflight-evidence-r1/REVIEW.json").is_file():
        pending.append("Receive the delivered read-only native preflight report for current datax-02/workload/PostgreSQL identities")
    pending.extend([
        "Run isolated network, Host, Frontend and backend/DB native qualification using the final six verified images; no further preload is required" if (ROOT / "native-preload-complete-evidence-r1/REVIEW.json").is_file() else "Qualify the corrected snapshot field distinction and complete only the three remaining Runner/Host image preloads" if (ROOT / "native-remaining-preload-evidence-r1/REVIEW.json").is_file() else "Complete the remaining four image preloads using the reviewed continuation; frontend and forward Gateway already verified" if (ROOT / "native-gateway-recovery-evidence-r1/REVIEW.json").is_file() else "Run one reviewed Gateway-only attempt with retained subprocess diagnostics, then review before continuing other roles" if (ROOT / "native-phase-b-diagnostic-evidence-r1/REVIEW.json").is_file() else "Diagnose the partial phase-B Gateway import failure using existing evidence; frontend preload passed, no blind import retry" if (ROOT / "native-phase-b-attempt-r1/REVIEW.json").is_file() else "Run the delivered phase-B-only continuation on datax-02 and reconcile six complete-image receipts; phase A already passed, fresh checks still required" if (ROOT / "native-phase-b-resume-r1/ROOT-DELIVERY-REVIEW.json").is_file() else "Preload the six existing exact candidates on datax-02 after bounded disk, digest and image-cache checks",
        "Qualify actual Host browser/import/Codex execution and exact Gateway/Runner/Frontend service startup on native ARM64",
        "Qualify the exact candidate and compatible rollback images on native ARM64 before production rollout",
        "Complete isolated enterprise end-to-end acceptance and database backup/restore/release procedure"
    ])
    continuation = read(ROOT / "CONTINUATION.json")
    continuation["open_conditions"] = pending
    continuation["next_step"] = "NATIVE_ARM64_ISOLATED_QUALIFICATION_AND_RELEASE_PREPARATION"
    continuation["current_cri_followup"]["status"] = "TWO_SANDBOX_CONFIG_IDENTITIES_MATCH_GATEWAY_MANIFEST_CONFIG_SUPPLEMENT_RECONCILED"
    continuation["current_cri_followup"]["gateway_supplement"] = ref(ROOT / "GATEWAY-BASE-IDENTITY-20261017.json")
    continuation["current_preparation_progress"] = ref(ROOT / "CURRENT-PREPARATION-PROGRESS.json")
    continuation["gateway_export_followup"] = result["gateway"]
    write(ROOT / "CONTINUATION.json", continuation)
    merge = read(MERGE / "MERGE-STATE.json")
    merge["status"] = "SIX_CANDIDATES_BUILT_LOCAL_DATABASE_QUALIFIED_NATIVE_RUNTIME_PENDING" if host_candidate_review.is_file() and (ROOT / "ACTUAL-IMAGE-QUALIFICATION-RECONCILIATION.json").is_file() else "CANDIDATE_PREPARATION_IN_PROGRESS"
    merge["pending"] = pending
    merge["current_preparation_progress"] = ref(ROOT / "CURRENT-PREPARATION-PROGRESS.json")
    write(MERGE / "MERGE-STATE.json", merge)
    print(json.dumps({"gateway": result["gateway"]["status"], "frontend": frontend_status, "host": result["agent_host"]["status"],
                      "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
