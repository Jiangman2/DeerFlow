"""Package the paired update using pinned controller and actual field receipts."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

BASE_SHA = "191a528ef63cd71229de1f12a02024249fa83fccfb3bde52aefe384eec039034"


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def without_entry(text):
    tree = ast.parse(text)
    last = tree.body[-1]
    assert isinstance(last, ast.If) and ast.get_source_segment(text, last.test) in ('__name__ == "__main__"', "__name__ == '__main__'")
    return "\n".join(text.splitlines()[:last.lineno - 1]) + "\n"


def build(base, folder):
    assert hashlib.sha256(base).hexdigest() == BASE_SHA
    source = base.decode()
    scope = {"__name__": "base_validation"}
    exec(compile(source, "<base>", "exec"), scope)
    release = json.loads((folder / "data-qa-glm-release-20261021-r1.json").read_text(encoding="utf-8"))
    acceptance = json.loads((folder / "data-qa-glm-candidate-acceptance-20261021-r2.json").read_text(encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("resume_builder", folder / "package-data-qa-glm-resumption.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    preparation = module.build(folder).decode()
    candidate = (folder / "check-data-qa-glm-candidate.py").read_text(encoding="utf-8")
    # Same accepted checks, now reading the actual loaded config mount rather
    # than the temporary Job mount. Network denial begins after existing HTTP/DB
    # read acceptance completes, and affects only the short-lived child.
    candidate = replace_once(candidate, 'pathlib.Path("/candidate/config.yaml")', 'AppConfig.resolve_config_path()')
    files = release["profile"]["tail_files"]["gateway-forward"]["files"]
    # The preparation artifact pins the whole ConfigMap. The preflight derives
    # its config.yaml digest only after validating that immutable resource.
    runtime = scope["SOURCES"]["runtime"].decode()
    runtime = "DATA_QA_FILES = " + repr(files) + "\n" + candidate + "\n" + (folder / "check-data-qa-agent-references.py").read_text(encoding="utf-8") + "\n" + runtime
    runtime = replace_once(runtime, "    value['brand']=brand_result", """    if payload.get('references'):
        from deerflow.config.paths import get_paths
        value['agent_references'] = agent_references(get_paths().base_dir)
    if payload.get('data_qa'):
        value['data_qa'] = check_candidate(DATA_QA_FILES, payload['config_file_sha256'], require)
    value['brand']=brand_result""")
    assert len(runtime.encode()) < 110000
    role = release["profile"]["roles"][0]
    # Joint Data QA layers preserve the already qualified four GLM files.
    runtime_scope = {}
    for node in ast.parse(scope["SOURCES"]["runtime"].decode()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STREAM_FILES" for t in node.targets):
            runtime_scope = ast.literal_eval(node.value)
    assert runtime_scope == {p: v for p, v in files.items() if "/models/" in p}
    sources = dict(scope["SOURCES"], runtime=runtime.encode())
    constants = dict(
        ACCEPTANCE=acceptance,
        PREPARATION_SOURCE=preparation,
        PLAN_SOURCE=(folder / "data-qa-glm-deployment-plan.py").read_text(encoding="utf-8"),
        SOURCES=sources,
        PINS={k: dict(bytes=len(v), sha256=hashlib.sha256(v).hexdigest()) for k, v in sources.items()},
        PRELOADS=json.loads((folder / "data-qa-glm-preloads-20261021-r1.json").read_text(encoding="utf-8")),
        MD=role["manifest_digest"], CD=role["config_digest"], TAG=role["tag"],
        NEW="deerflow.local/deer-flow-gateway@" + role["manifest_digest"],
    )
    # Keep the original controller, then replace only reviewed release-specific
    # functions. The base standalone main guard is never executed during loading.
    result = without_entry(source)
    result += "\nlegacy_configuration_gate = configuration_gate\n"
    result += "\n".join(k + " = " + repr(v) for k, v in constants.items()) + "\n"
    gates = (folder / "data-qa-glm-activation-gates.py").read_text(encoding="utf-8")
    result += gates + "\n"
    functions = {n.name: ast.get_source_segment(source, n) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    execute = functions["execute"]
    execute = replace_once(execute, 'dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=False)', 'dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=False, references=True)')
    execute = replace_once(execute, '        check_runtime(value, False)', '        r.setdefault("agent_reference_observations", []).append(dict(pod=pod["metadata"]["name"], observation=value.get("agent_references")))\n        check_runtime(value, False)\n        check_agent_inventory(value)')
    execute = replace_once(execute, '    require(all(binding(c.get(role)) == binding(v) for role, v in baseline.items()), "unrelated_changed_before_patch")', '    require(len({x["observation"]["agent_references"]["inventory_sha256"] for x in r["preflight_observations"]}) == 1, "agent_reference_views_disagree")\n    require(all(binding(c.get(role)) == binding(v) for role, v in baseline.items()), "unrelated_changed_before_patch")')
    execute = replace_once(execute, 'dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=True)', 'dict(action="accept", typed_uuid_read=True, brand="new", stream_fixed=True, data_qa=True, config_file_sha256=r["config_file_sha256"])')
    execute = replace_once(execute, '        check_runtime(value, True)', '        check_runtime(value, True)\n        check_live_data_qa(value)')
    execute = execute.replace('GLM_STREAM', 'DATA_QA_GLM').replace('one-image-only-glm-stream-completion', 'paired-image-config-secret-data-qa-glm')
    execute = replace_once(execute, '        configuration_preserved=True,', '        other_configuration_preserved=True,\n        data_qa_runtime_accepted=70,\n        persisted_custom_agent_reference_inventory_pending=False,\n        business_values_verified=False,')
    result += execute + "\n\n"
    run = functions["run"]
    run = run.replace('portal-gateway-glm-stream-', 'portal-gateway-data-qa-glm-').replace('--activate-gateway-glm-stream-once', '--activate-gateway-data-qa-glm-once')
    run = replace_once(run, '        read_canary(parent)', '        read_canary(parent)\n        read_joint_acceptance(parent)')
    run = replace_once(run, '        field_acceptance=FIELD,', '        field_acceptance=FIELD,\n        joint_candidate_acceptance=ACCEPTANCE,')
    run = replace_once(run, '        provider_requests=0,', '        provider_requests=0,\n        persisted_custom_agent_reference_inventory_pending=True,')
    run = replace_once(run, 'isinstance(error, Refused) and re.fullmatch("[a-z0-9_-]{1,120}", code)', 'isinstance(error, (Refused, PREP["Refused"], ValueError)) and re.fullmatch("[A-Za-z0-9_-]{1,120}", code)')
    run = replace_once(run, '        "configuration_preserved",', '        "other_configuration_preserved",\n        "data_qa_runtime_accepted",\n        "business_values_verified",\n        "persisted_custom_agent_reference_inventory_pending",')
    run = replace_once(run, '        "report_path",', '        "agent_reference_observations",\n        "report_path",')
    result += run + '\n\nif __name__ == "__main__":\n    raise SystemExit(run())\n'
    checked = {"__name__": "packaged_validation"}
    exec(compile(result, "<joint-activation>", "exec"), checked)
    checked["preload_gate"]()
    assert checked["PRELOADS"] == checked["PREP"]["PRELOADS"]
    checked["candidate_receipt_gate"](acceptance)
    compile(runtime, "<joint-runtime>", "exec")
    return result.encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = build(args.base.read_bytes(), Path(__file__).parent)
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=str(args.output))))


if __name__ == "__main__":
    main()
