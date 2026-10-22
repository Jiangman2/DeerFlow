"""Package bounded reference diagnostics without altering the delivered controller."""

import argparse
import hashlib
import json
from pathlib import Path


def build(folder):
    scanner = (folder / "check-data-qa-agent-references.py").read_text(encoding="utf-8")
    assert scanner.count("def agent_references(root, now=None):") == 1
    scanner = scanner.replace("def agent_references(root, now=None):", "def agent_references(root, now=None, observer=None):")
    insertion = "            affected += bool(found)"
    assert scanner.count(insertion) == 1
    scanner = scanner.replace(insertion, insertion + "\n            if found and observer is not None:\n                observer(path.relative_to(root), raw, before)")
    probe = scanner + "\n" + (folder / "describe-data-qa-agent-references.py").read_text(encoding="utf-8")
    probe += '''
import contextlib,io,json
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from deerflow.config.paths import get_paths
        result = inspect_references(get_paths().base_dir, agent_references)
except Exception as exc:
    result = dict(status="INCOMPLETE", error_class=type(exc).__name__, raw_text_exported=False)
print(json.dumps(result,sort_keys=True,ensure_ascii=True))
'''
    compile(probe, "<probe>", "exec")
    result = "PROBE = " + repr(probe) + "\n" + (folder / "inspect-data-qa-agent-references.py").read_text(encoding="utf-8")
    compile(result, "<inspection>", "exec")
    return result.encode()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    raw = build(Path(__file__).parent)
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=str(args.output))))


if __name__ == "__main__":
    main()
