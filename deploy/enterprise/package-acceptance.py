"""Build the allowlisted diagnostic archive; never include env/config/credentials."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
FILES = {
    "run-acceptance.py": "deploy/enterprise/run-acceptance.py",
    "acceptance-remote.py": "deploy/enterprise/acceptance-remote.py",
    "portal-production-probe.py": "deploy/enterprise/portal-production-probe.py",
    "run-datax01-acceptance.sh": "deploy/enterprise/run-datax01-acceptance.sh",
    "ACCEPTANCE-RUNBOOK.txt": "deploy/enterprise/ACCEPTANCE-RUNBOOK.txt",
    "employee_featured_store.py": "backend/app/gateway/employee_featured_store.py",
    "runtime_config_store.py": "backend/app/channels/runtime_config_store.py",
    "robust_openai.py": "backend/packages/harness/deerflow/models/robust_openai.py",
    "enterprise_glm.py": "backend/packages/harness/deerflow/models/enterprise_glm.py",
    "enterprise_glm_probe.py": "backend/app/gateway/enterprise_glm_probe.py",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: (ROOT / source).read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8") for name, source in FILES.items()}
    payload["PACKAGE.SHA256"] = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in sorted(payload.items())).encode()
    with output.open("xb") as stream:
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for name, data in sorted(payload.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.uid, info.gid, info.mtime = len(data), 0o600, 0, 0, 0
                archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with Path(str(output) + ".sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(digest + "  " + output.name + "\n")
    print(json.dumps({"package": str(output), "sha256": digest, "files": len(payload), "production_mutation": False, "ready_for_rollout": False}))


if __name__ == "__main__":
    main()
