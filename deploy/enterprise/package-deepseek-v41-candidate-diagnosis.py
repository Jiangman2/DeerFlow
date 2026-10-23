"""Bind the observed 503 batch; isolate the max_tokens candidate for one retest."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

BASE_SHA = "3399918178cb1a614ce3ee5eb827f754b5b4b8ea8c690d3cbfc6df28f5c325ee"


def once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


GATE = """
original_claim_once = claim_once
def claim_once(parent, name, report):
    require(report.get('gateway_before') == dict(generation=382,spec_sha256='699119d2d3e7a2e30aad980d5c4f37560be9022b00059a2712b6512e4b3816ff',uid=GATEWAY_UID), 'candidate_gateway_baseline_changed')
    fd = os.open(str(parent),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    child = None
    try:
        marker = json.loads(read_private(fd,'portal-deepseek-v41-transport-20261021-r1.once',4096))
        require(marker == dict(operation='ce9c7e15fa854b4da7d417a01e774f96'),'prior_marker_changed')
        child = os.open('portal-deepseek-v41-transport-ce9c7e15fa854b4da7d417a01e774f96',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
        info = os.fstat(child)
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,'prior_directory_permissions')
        raw = read_private(child,'REPORT.json',16384)
        require(len(raw)==12241 and hashlib.sha256(raw).hexdigest()=='c07805d387be55cd12fefc01495da39d47719dfb060722f6fdd8193f9cadb7d6','prior_report_changed')
        old = json.loads(raw)
        require(old.get('provider_outcome_may_be_uncertain') is False and old.get('configuration_modified') is False and old.get('secret_created') is False and old.get('provider_requests_attempted') == 6,'prior_outcome_not_verified')
    finally:
        if child is not None:os.close(child)
        os.close(fd)
    report['resumed_diagnostic_operation']='ce9c7e15fa854b4da7d417a01e774f96'
    report['candidate_only_in_child']=True
    report['candidate_source_sha256']=CANDIDATE_SHA
    report['request_deadline_seconds']=180
    original_claim_once(parent,name,report)
"""


def build(root, base):
    assert hashlib.sha256(base).hexdigest() == BASE_SHA
    source = base.decode()
    scope = {"__name__": "r1_validation"}
    exec(compile(source, "<r1>", "exec"), scope)
    nodes = ast.parse(source).body
    main = next(ast.get_source_segment(source, n) for n in nodes if isinstance(n, ast.FunctionDef) and n.name == "main")
    result = "\n".join(source.splitlines()[: nodes[-1].lineno - 1]) + "\n"
    candidate = (root / "backend/packages/harness/deerflow/models/enterprise_deepseek_v41.py").read_text(encoding="utf-8")
    digest = hashlib.sha256(candidate.encode()).hexdigest()
    remote = scope["DIAGNOSTIC_REMOTE"]
    insertion = """
 import types
 import deerflow.models.factory as factory
 import deerflow.models.robust_openai as adapter
 assert hashlib.sha256(pathlib.Path(factory.__file__).read_bytes()).hexdigest()=='8009fc0ea267d9520aec2eef9c8a6e1daa1d454ddab028c6d70eb68da8070510','FACTORY_BASELINE_CHANGED'
 assert hashlib.sha256(pathlib.Path(adapter.__file__).read_bytes()).hexdigest()=='8a41b677b843b6c8e58433f1725378c312af0315711bc1ca62422d55421f2a52','ADAPTER_BASELINE_CHANGED'
 assert hashlib.sha256(data['candidate'].encode()).hexdigest()==data['candidate_sha256'],'CANDIDATE_HASH_CHANGED'
 module=types.ModuleType('deerflow.models.enterprise_deepseek_v41')
 exec(compile(data['candidate'],'<candidate>','exec'),module.__dict__)
 sys.modules[module.__name__]=module
"""
    remote = once(remote, "assert set(data)=={'key','source','evidence','profile','operation'}", "assert set(data)=={'key','source','evidence','profile','operation','candidate','candidate_sha256'}")
    remote = once(remote, " import httpx\n", " import httpx\n" + insertion)
    remote = once(remote, "signal.alarm(600)", "signal.alarm(1150)")
    remote = once(remote, "timeout=90", "timeout=180")
    evidence = scope["EVIDENCE_SOURCE"]
    evidence = once(evidence, "timeout=45", "timeout=180")
    evidence = once(evidence, "request_deadline=90", "request_deadline=180")
    evidence = once(evidence, 'max_tokens=body.get("max_tokens"),', 'max_tokens=body.get("max_tokens"), max_completion_tokens=body.get("max_completion_tokens"),')
    evidence = once(
        evidence,
        "and all(s['model_matches'] and s['stream'] is True for s in shapes)",
        "and all(s['model_matches'] and s['stream'] is True and s['max_tokens']==1000 and s['max_completion_tokens'] is None and not s['unexpected_parameter_names'] for s in shapes)",
    )
    evidence = once(
        evidence,
        "item['error_class'] = type(info['exception']).__name__",
        "item['error_class'] = type(info['exception']).__name__\n                        if item['error_class']=='GeneratorExit' and row.get('done_seen') and row.get('terminal_reasons'):\n                            item['event']='sdk_stream_closed_after_done'",
    )
    profile = dict(scope["PROFILE"], use="deerflow.models.enterprise_deepseek_v41:EnterpriseDeepSeekV41Chat")
    values = dict(CANDIDATE_SOURCE=candidate, CANDIDATE_SHA=digest, PROFILE=profile, EVIDENCE_SOURCE=evidence, DIAGNOSTIC_REMOTE=remote, DIAGNOSTIC_ONCE="portal-deepseek-v41-transport-20261021-r2.once")
    result += "\n".join(k + " = " + repr(v) for k, v in values.items()) + "\n"
    helper = (root / "deploy/enterprise/prepare-deepseek-v41-provider.py").read_text(encoding="utf-8")
    result += next(ast.get_source_segment(helper, n) for n in ast.parse(helper).body if isinstance(n, ast.FunctionDef) and n.name == "read_private") + "\n" + GATE + "\n"
    main = once(main, "portal-deepseek-v41-transport-diagnosis.v1", "portal-deepseek-v41-transport-diagnosis.v2")
    main = once(main, 'operation=report["operation"] + "-" + str(i + 1)', 'operation=report["operation"] + "-" + str(i + 1), candidate=CANDIDATE_SOURCE, candidate_sha256=CANDIDATE_SHA')
    main = once(main, "timeout=630", "timeout=1200")
    result += main + '\n\nif __name__ == "__main__":\n    sys.exit(main())\n'
    ast.parse(result, feature_version=(3, 7))
    return result.encode()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    raw = build(Path(__file__).resolve().parents[2], args.base.read_bytes())
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=str(args.output))))
