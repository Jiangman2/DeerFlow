"""Build r2: scoped reference migration + pinned r1 paired activation."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

BASE_SHA = "157e68b96459d065df544aa7347b7fd343b25c985564def6c85eb6a3436ae7ec"


def once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def build(base, folder):
    assert hashlib.sha256(base).hexdigest() == BASE_SHA
    source = base.decode()
    tree = ast.parse(source)
    functions = {n.name: ast.get_source_segment(source, n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    assert isinstance(tree.body[-1], ast.If)
    source = "\n".join(source.splitlines()[:tree.body[-1].lineno - 1]) + "\n"
    pins = json.loads((folder / "data-qa-agent-reference-pins-20261021.json").read_text(encoding="utf-8"))
    scanner = (folder / "check-data-qa-agent-references.py").read_text(encoding="utf-8")
    scanner = once(scanner, "def agent_references(root, now=None):", "def agent_references(root, now=None, observer=None):")
    scanner = once(scanner, "            affected += bool(found)", "            affected += bool(found)\n            if found and observer is not None:\n                observer(path.relative_to(root), raw, before)")
    probe = "REFERENCE_PINS = " + repr(pins) + "\n" + scanner + "\n" + (folder / "migrate-data-qa-agent-references.py").read_text(encoding="utf-8")
    probe += '''
import contextlib,io,signal,sys
try:
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(60)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from deerflow.config.paths import get_paths
        result = migration(get_paths().base_dir, REFERENCE_PINS, agent_references, sys.argv[1], sys.argv[2])
except BaseException as exc:
    code = str(exc)
    result = dict(status="INCOMPLETE", error_class=type(exc).__name__, error_code=code if isinstance(exc,ValueError) and re.fullmatch("[A-Z_]{1,80}", code) else "MIGRATION_FAILED", raw_text_exported=False)
finally:
    signal.alarm(0)
print(json.dumps(result,sort_keys=True,ensure_ascii=True))
'''
    compile(probe, "<migration>", "exec")
    assert len(probe.encode()) < 110000
    constants = dict(REFERENCE_PINS=pins, REFERENCE_PROBE=probe, REFERENCE_ONCE="portal-data-qa-agent-reference-activation-20261021-r2.once")
    source += "\n".join(k + " = " + repr(v) for k, v in constants.items()) + "\n"
    execute = once(functions["execute"], "def execute(", "def execute_with_reference_hooks(")
    execute = once(execute, '    r["preflight_observations"] = []', '    start_reference_migration(c, pods)\n    r["preflight_observations"] = []')
    execute = once(execute, '    c.helper.write_new(c.parent, ONCE,', '    for reference_pod in selected_two(pods):\n        reference_call(c, reference_pod, "verify")\n    c.helper.write_new(c.parent, ONCE,')
    execute = once(execute, '    c.save("AFTER-GATEWAY.private.json", final)', '    for reference_pod in selected_two(finalpods):\n        reference_call(c, reference_pod, "verify")\n    c.save("AFTER-GATEWAY.private.json", final)')
    source += execute + "\n" + (folder / "data-qa-reference-activation-hooks.py").read_text(encoding="utf-8") + "\n"
    run = functions["run"]
    run = once(run, '["--activate-gateway-data-qa-glm-once", "--tasks-drained"]', '["--activate-gateway-data-qa-glm-once", "--tasks-drained", "--agent-edits-paused"]')
    run = once(run, '        r["tasks_drained_operator_attested"] = True', '        r["tasks_drained_operator_attested"] = True\n        r["agent_edits_paused_operator_attested"] = True')
    run = once(run, '        require(not os.path.lexists("/var/lib/" + ONCE), "activation_already_claimed_review_existing_report")', '        require(not os.path.lexists("/var/lib/" + ONCE) and not os.path.lexists("/var/lib/" + REFERENCE_ONCE), "activation_already_claimed_review_existing_report")')
    run = once(run, 'folder = "portal-gateway-data-qa-glm-activation-r1-"', 'folder = "portal-gateway-data-qa-glm-activation-r2-"')
    run = once(run, 'schema="portal-gateway-data-qa-glm-activation.v1"', 'schema="portal-gateway-data-qa-glm-activation.v2"')
    run = once(run, '        "agent_reference_observations",', '        "agent_reference_observations",\n        "agent_edits_paused_operator_attested",\n        "agent_reference_migration",\n        "agent_reference_mutation_requested",\n        "agent_reference_outcome_uncertain",\n        "agent_reference_restored",\n        "agent_reference_recovery_required",')
    source += run + '\n\nif __name__ == "__main__":\n    raise SystemExit(run())\n'
    compile(source, "<activation-r2>", "exec")
    return source.encode()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    raw = build(args.base.read_bytes(), Path(__file__).parent)
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=str(args.output))))
