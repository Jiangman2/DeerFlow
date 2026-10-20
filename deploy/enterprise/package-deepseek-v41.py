"""Create the standalone preparation tool; never embeds a credential."""

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
    profile = yaml.safe_load(
        (root / "deploy/enterprise/deepseek-v4.1-flash.model.yaml").read_text(
            encoding="utf-8"
        )
    )["models"][0]
    source = (root / "backend/app/gateway/enterprise_deepseek_v41_probe.py").read_text(
        encoding="utf-8"
    )
    template = (root / "deploy/enterprise/prepare-deepseek-v41-provider.py").read_text(
        encoding="utf-8"
    )
    assert profile["api_key"] == "$DEEPSEEK_V4_1_FLASH_API_KEY"
    template = template.replace(
        "PROFILE = None  # Filled by the reproducible packager.",
        "PROFILE = " + repr(profile),
    )
    template = template.replace("PROBE_SOURCE = None", "PROBE_SOURCE = " + repr(source))
    ast.parse(template, feature_version=(3, 7))
    raw = template.encode("utf-8")
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(
        json.dumps(
            dict(
                path=str(args.output),
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                credentials_embedded=False,
            )
        )
    )


if __name__ == "__main__":
    main()
