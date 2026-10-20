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
    parser.add_argument("--resume-noop-receipt", type=Path, help="Operator-returned operation/bytes/sha256 for an exact no-action failure")
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
    if args.resume_noop_receipt:
        prior = json.loads(args.resume_noop_receipt.read_text(encoding="utf-8"))
        assert set(prior) == {"operation", "bytes", "sha256"}
        assert len(prior["operation"]) == 32 and all(c in "0123456789abcdef" for c in prior["operation"])
        assert type(prior["bytes"]) is int and 0 < prior["bytes"] <= 65536
        assert len(prior["sha256"]) == 64 and all(c in "0123456789abcdef" for c in prior["sha256"])
        template = template.replace("PRIOR_ATTEMPT = None  # Optional pinned, no-provider/no-Secret failure receipt.", "PRIOR_ATTEMPT = " + repr(prior))
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
