"""Reference migration lifecycle around the unchanged paired controller."""

# ruff: noqa: F821 -- packaged alongside the pinned controller.


def reference_call(c, pod, action):
    print(json.dumps(dict(phase="PINNED_AGENT_REFERENCE_MIGRATION", action=action)), flush=True)
    def identity():
        p = c.call("reference-pod-identity", ["get", "pod", pod["metadata"]["name"], "-o", "json"])
        state = next(x for x in p["status"]["containerStatuses"] if x["name"] == "gateway")
        before = next(x for x in pod["status"]["containerStatuses"] if x["name"] == "gateway")
        require(p["metadata"]["uid"] == pod["metadata"]["uid"] and not p["metadata"].get("deletionTimestamp") and state.get("ready") is True and state.get("containerID") == before.get("containerID"), "reference_pod_changed")

    identity()
    if action == "apply":
        c.save("AGENT-MIGRATION-INTENT.private.json", dict(operation=c.report["operation"], pod=pod["metadata"]["name"], pins=REFERENCE_PINS))
        c.report.update(agent_reference_mutation_requested=True, agent_reference_outcome_uncertain=True, business_state_write_requested=True)
    value = c.call("agent-references-" + action, ["exec", pod["metadata"]["name"], "-c", "gateway", "--", "/app/backend/.venv/bin/python", "-I", "-B", "-c", REFERENCE_PROBE, c.report["operation"], action], timeout=90)
    if action == "apply":
        # A returned JSON means the one-shot child has exited its write path.
        c.report["agent_reference_outcome_uncertain"] = False
        c.report["agent_reference_apply_returned"] = True
    c.report.setdefault("agent_reference_migration", []).append(dict(action=action, pod=pod["metadata"]["name"], observation=value))
    require(value.get("status") == ("RESTORED" if action == "restore" else "MIGRATION_VERIFIED") and value.get("files") == 2 and value.get("backup_verified") is True, "agent_reference_" + action + "_failed")
    identity()
    return value


def start_reference_migration(c, pods):
    # Bind the exact earlier no-patch failure as well as today's line pins.
    value = private_read(c.parent, "portal-gateway-data-qa-glm-activation-r1-efa5e03631e046b08eb30497198936bd", "REPORT.json", 26307, dict(bytes=26307, sha256="fe2959adb97e8bf4f78c0a6a93f7d9d740785ce0e068c77a73be5e1900b21fd8"))
    require(value.get("patch_requested") is False and value.get("patch_request_count") == 0 and value.get("patch_may_have_applied") is False and value.get("error_code") == "persisted_agent_old_references_or_incomplete", "reference_blocker_receipt_mismatch")
    require(binding(c.get("gateway")) == CURRENT_BINDING, "gateway_changed_before_reference_migration")
    c.reference_pod = selected_two(pods)[0]
    # Separate once marker BEFORE user-file writes. Never erase this marker to retry.
    c.helper.write_new(c.parent, REFERENCE_ONCE, canonical(dict(operation=c.report["operation"], directory=c.report["directory"])))
    reference_call(c, c.reference_pod, "apply")


def execute(c, backend, now=time.monotonic, sleep=time.sleep):
    try:
        execute_with_reference_hooks(c, backend, now=now, sleep=sleep)
    except BaseException:
        r = c.report
        if r.get("agent_reference_apply_returned") and not r.get("patch_requested") and not r.get("patch_may_have_applied"):
            try:
                # Do not restore old names under somebody else's changed Gateway.
                require(binding(c.get("gateway")) == CURRENT_BINDING, "gateway_changed_reference_restore_refused")
                reference_call(c, c.reference_pod, "restore")
                r["agent_reference_restored"] = True
            except BaseException:
                r["agent_reference_restored"] = False
                r["agent_reference_recovery_required"] = True
        elif r.get("agent_reference_mutation_requested"):
            # A timed out exec may still be finishing; a submitted Deployment
            # patch may already be active. Neither permits automatic restoration.
            r["agent_reference_recovery_required"] = True
        raise
