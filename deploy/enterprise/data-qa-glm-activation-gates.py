"""Additional joint-release gates, packaged with the qualified rollout controller."""

# ruff: noqa: F821 -- populated by the package builder.

ONCE = "portal-gateway-data-qa-glm-activation-20261021-r1.once"
PREP = {"__name__": "isolated_resource_validation"}
exec(compile(PREPARATION_SOURCE, "<resource-validation>", "exec"), PREP)
PLANNER = {"__name__": "paired_spec_plan"}
exec(compile(PLAN_SOURCE, "<paired-plan>", "exec"), PLANNER)


def preload_gate():
    PREP["preload_gate"]()


def candidate_receipt_gate(value):
    require(value.get("schema") == "portal-data-qa-glm-resumption.v2" and value.get("status") == "DATA_QA_GLM_PREPARED_CANDIDATE_ACCEPTED_NOT_DEPLOYED" and value.get("exitcode") == 0, "candidate_receipt_not_accepted")
    require(value.get("gateway_before") == value.get("gateway_after") == CURRENT_BINDING and value.get("candidate_accepted") == 2 and value.get("provider_requests") == 0 and value.get("deployment_patch_requested") is False, "candidate_baseline_changed")
    require(value.get("prepared_config") == ACCEPTANCE["prepared_config"] and value.get("prepared_secret") == ACCEPTANCE["prepared_secret"], "candidate_resource_identity")
    checks = value.get("candidate_checks", [])
    require(len(checks) == 2 and len({x["uid"] for x in checks}) == 2, "two_candidate_receipts_required")
    for row in checks:
        observed = row["observation"]
        require(observed.get("status") == "PASS" and observed.get("source_files") == 15 and observed.get("data_tools") == ["datalake_data_qa", "datalake_smart_qa"] and observed.get("dedicated_key_resolved") is True and observed.get("disabled_button_hint") == observed.get("empty_error_and_delegate_scope") == "PASS" and observed.get("external_paths_denied") == 7 and observed.get("provider_requests") == 0, "candidate_checks_incomplete")
        stream = observed.get("stream_completion", {})
        require(stream.get("status") == "PASS" and stream.get("fixed") is True and stream.get("offline_terminal_and_done_checked") is True, "candidate_stream_incomplete")


def read_joint_acceptance(parent):
    PREP["sealed_evidence"]()
    folder = "portal-data-qa-glm-resumption-" + ACCEPTANCE["operation"]
    value = private_read(parent, folder, "REPORT.json", ACCEPTANCE["report_bytes"], {k: ACCEPTANCE["report_" + k] for k in ("bytes", "sha256")})
    candidate_receipt_gate(value)
    return value


def plan(deployment):
    return PLANNER["activation_plan"](deployment, baseline=CURRENT_BINDING, config_receipt=ACCEPTANCE["prepared_config"], secret_receipt=ACCEPTANCE["prepared_secret"])


def configuration_gate(c, backend):
    # Retain the qualified GLM credential/old-config checks as well as the new
    # immutable resources. No Secret value is returned by kubectl.
    legacy_configuration_gate(c, backend)

    class View:
        def get(self, kind, name):
            return c.call("joint-get-" + kind, ["get", kind, name, "-o", "json"])

        def call(self, phase, args, raw=False):
            return c.call("joint-" + phase, args, raw=raw)

    original = PREP["read_failed_preparation"]()
    config, reused = PREP["existing_resources"](View(), original)
    require(reused["config"] == ACCEPTANCE["prepared_config"] and reused["secret"] == ACCEPTANCE["prepared_secret"], "prepared_resource_changed")
    c.report["config_file_sha256"] = pin(config["data"]["config.yaml"].encode())["sha256"]
    c.report.update(prepared_config=reused["config"], prepared_secret=reused["secret"])


def check_agent_inventory(value):
    item = value.get("agent_references", {})
    require(item.get("status") == "PASS" and item.get("affected_files") == 0 and item.get("modified") is False and re.fullmatch("[a-f0-9]{64}", item.get("inventory_sha256", "")), "persisted_agent_old_references_or_incomplete")


def check_live_data_qa(value):
    check = value.get("data_qa", {})
    require(check.get("status") == "PASS" and check.get("source_files") == 15 and check.get("dedicated_key_resolved") is True and check.get("data_tools") == ["datalake_data_qa", "datalake_smart_qa"] and check.get("external_paths_denied") == 7 and check.get("disabled_button_hint") == check.get("empty_error_and_delegate_scope") == "PASS" and check.get("provider_requests") == 0, "data_qa_runtime_acceptance_failed")
