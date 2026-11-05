"""Package an installed-adapter diagnostic; no provider code or credentials."""

import argparse
import hashlib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).parent
    source = (root / "glm-usage-timing-probe.py").read_text(encoding="utf-8")
    bundle = {"source": source, "sha256": hashlib.sha256(source.encode()).hexdigest()}
    template = (root / "probe-glm-usage-timing.py").read_text(encoding="utf-8")
    marker = "BUNDLE = None  # Filled by package-glm-usage-timing.py."
    assert template.count(marker) == 1
    payload = template.replace(marker, "BUNDLE = " + repr(bundle)).encode()
    with args.output.open("xb") as stream:
        stream.write(payload)
    print(
        {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "credentials_embedded": False,
        }
    )


if __name__ == "__main__":
    main()
