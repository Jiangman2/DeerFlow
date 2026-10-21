"""Build a separate, report-bound resume; never overwrite the delivered r1."""

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path


def build(folder):
    spec = importlib.util.spec_from_file_location("preparation_builder", folder / "package-data-qa-glm-preparation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = module.build(folder).decode()
    tree = ast.parse(base)
    last = tree.body[-1]
    assert isinstance(last, ast.If) and ast.get_source_segment(base, last.test) == '__name__ == "__main__"'
    base = "\n".join(base.splitlines()[: last.lineno - 1]) + "\n"
    failure = json.loads((folder / "data-qa-glm-preparation-failure-20261021-r1.json").read_text(encoding="utf-8"))
    assert failure["report_bytes"] == 6520 and failure["report_sha256"] == "5e389a9a1710b239159e3d4872329943db55add3fcc52f66af7ad41c55780c98"
    code = base + "\nFAILURE = " + repr(failure) + "\nREUSE_SOURCE = " + repr((folder / "data-qa-glm-reuse.py").read_text(encoding="utf-8")) + "\n"
    code += (folder / "resume-data-qa-glm.py").read_text(encoding="utf-8")
    namespace = {"__name__": "package_validation"}
    exec(compile(code, "<resumption>", "exec"), namespace)
    namespace["preload_gate"]()
    return code.encode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = build(Path(__file__).parent)
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), path=str(args.output))))


if __name__ == "__main__":
    main()
