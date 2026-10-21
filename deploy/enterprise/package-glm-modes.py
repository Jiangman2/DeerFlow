"""Package public GLM candidate sources only; never includes credentials."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    text = (root / "deploy/enterprise/diagnose-glm-modes.py").read_text(encoding="utf-8")
    values = {
        "ADAPTER_SOURCE": (root / "backend/packages/harness/deerflow/models/enterprise_glm.py").read_text(encoding="utf-8"),
        "PROBE_SOURCE": (root / "backend/app/gateway/enterprise_glm_probe.py").read_text(encoding="utf-8"),
        "PROFILE": yaml.safe_load((root / "deploy/enterprise/glm-5.3-flash-switchable.model.yaml").read_text(encoding="utf-8"))["models"][0],
    }
    assert values["PROFILE"]["api_key"] == "$GLM_5_3_FLASH_API_KEY"
    for name, value in values.items():
        assert text.count(name + " = None") == 1
        text = text.replace(name + " = None", name + " = " + repr(value))
    ast.parse(text, feature_version=(3, 7))
    raw = text.encode("utf-8")
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), credentials_embedded=False)))


if __name__ == "__main__":
    main()
