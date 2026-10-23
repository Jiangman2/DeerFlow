"""Package a credential-free, source-bound GLM candidate verification tool."""

import argparse
import hashlib
import pathlib
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[2]
    model_dir = "backend/packages/harness/deerflow/models/"
    base = "5b17062fc"
    names = ("output_safety", "robust_openai", "enterprise_glm")
    bundle = {"base_hashes": {}, "sources": {}}
    for name in names:
        raw = subprocess.check_output(["git", "show", base + ":" + model_dir + name + ".py"], cwd=root).replace(b"\r\n", b"\n")
        bundle["base_hashes"][name] = hashlib.sha256(raw).hexdigest()
    for name in names + ("glm_stream_guard",):
        source = (root / (model_dir + name + ".py")).read_text(encoding="utf-8")
        bundle["sources"][name] = {"source": source, "sha256": hashlib.sha256(source.encode()).hexdigest()}
    source = (root / "backend/app/gateway/enterprise_glm_probe.py").read_text(encoding="utf-8")
    bundle["probe"] = {"source": source, "sha256": hashlib.sha256(source.encode()).hexdigest()}
    template = (root / "deploy/enterprise/probe-glm-stream-completion.py").read_text(encoding="utf-8")
    template = template.replace("BUNDLE = None  # Filled by package-glm-stream-completion.py.", "BUNDLE = " + repr(bundle))
    with args.output.open("xb") as stream:
        stream.write(template.encode())
    print({"path": str(args.output), "bytes": len(template.encode()), "sha256": hashlib.sha256(template.encode()).hexdigest(), "credentials_embedded": False})


if __name__ == "__main__":
    main()
