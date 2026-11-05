"""Package the source-bound canary without any credentials."""

import argparse
import hashlib
import pathlib
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[2]
    prefix = "backend/packages/harness/deerflow/"
    base = {}
    for name in ("community/appforge/tools.py", "community/appforge/__init__.py", "config/tool_config.py", "tools/tools.py", "agents/middlewares/tool_error_handling_middleware.py"):
        commit = "739e17f9a4d5a8d1bf505a5934b5c3883a23a24c" if name == "community/appforge/tools.py" else "e09bf6d3c"
        raw = subprocess.check_output(["git", "show", commit + ":" + prefix + name], cwd=root)
        base[name] = hashlib.sha256(raw).hexdigest()
    modules = {}
    for name in ("config.datalake_tools", "runtime.datalake_policy", "community.appforge.data_qa_stream", "community.appforge.tools"):
        raw = (root / (prefix + name.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        module_name = "deerflow." + ("community.appforge._candidate_tools" if name == "community.appforge.tools" else name)
        modules[module_name] = {"source": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}
    bundle = {"base_sources": base, "modules": modules}
    template = (root / "deploy/enterprise/probe-data-qa-candidate.py").read_text(encoding="utf-8")
    source = template.replace("BUNDLE = None  # Filled by package-data-qa-candidate.py.", "BUNDLE = " + repr(bundle)).encode()
    with args.output.open("xb") as output:
        output.write(source)
    print({"bytes": len(source), "sha256": hashlib.sha256(source).hexdigest(), "credentials_embedded": False})


if __name__ == "__main__":
    main()
