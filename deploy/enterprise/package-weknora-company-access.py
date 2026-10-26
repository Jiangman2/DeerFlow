"""Build a self-contained one-user canary bound to the native creation receipt."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def substitute(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError("CANARY_TEMPLATE_CHANGED")
    return source.replace(before, after)


def build():
    directory = ROOT / "deploy" / "enterprise"
    child = (directory / "verify-weknora-company-access-child.py").read_text(encoding="utf-8")
    helpers = (directory / "bootstrap-weknora-company-knowledge-child.py").read_text(encoding="utf-8")
    policy = (ROOT / "backend/app/gateway/weknora_company_knowledge.py").read_text(encoding="utf-8")
    inventory = (directory / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8")
    child = substitute(child, "HELPERS = None  # Packaged bootstrap helpers; never run their main.", "HELPERS = " + repr(helpers))
    child = substitute(child, "POLICY = None  # Packaged candidate enrollment source.", "POLICY = " + repr(policy))
    child = substitute(child, "INVENTORY = None  # Read-only all-user inventory; no grants.", "INVENTORY = " + repr(inventory))
    host = (directory / "bootstrap-weknora-company-knowledge-host.py").read_text(encoding="utf-8")
    host = substitute(host, '"""Pinned native WeKnora resource bootstrap. Host Python 3.7+.\n\nThe packaged version embeds CHILD. This repository template is not executable\nuntil packaged. It never patches deployments, creates accounts or resets keys.\n"""', '"""Verify authorized ordinary viewer access; one native membership may be added.\n\nNo documents, model calls, account changes, deployments or database migrations.\nHost Python 3.7+; do not replay an uncertain attempt.\n"""')
    host = substitute(host, 'DIRECTORY = Path("/var/lib/portal-weknora-company-bootstrap-20261024-r1")', 'DIRECTORY = Path("/var/lib/portal-weknora-company-viewer-canary-20261024-r1")')
    host = substitute(host, '{"administrator_email": "jiangman2@cnooc.com.cn", "automatic_retry": False}', '{"verification_email": "bianqy@cnooc.com.cn", "automatic_retry": False}')
    host = host.replace("ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED", "ORDINARY_VIEWER_CANDIDATE_ACCEPTED_NOT_DEPLOYED")
    host = host.replace("portal-weknora-company-bootstrap-host.v1", "portal-weknora-company-viewer-canary-host.v1")
    host = host.replace("portal-weknora-company-bootstrap.v1", "portal-weknora-company-viewer-canary.v1")
    host = host.replace("--create-company-knowledge-once", "--verify-company-viewer-once")
    host = host.replace("NATIVE_BOOTSTRAP_INCOMPLETE", "NATIVE_VIEWER_CANARY_INCOMPLETE").replace("BOOTSTRAP_INCOMPLETE", "VIEWER_CANARY_INCOMPLETE")
    host = substitute(host, 'observation.get("resource_write_may_have_applied", True)', 'observation.get("membership_mutation_may_have_applied", True)')
    evidence = json.loads((directory / "weknora-company-bootstrap-field-20261024-r1.json").read_text(encoding="utf-8"))
    validation = '''def check_creation_receipt():
    path = Path(%(path)r)
    require(path.is_file() and not path.is_symlink(), "CREATION_RECEIPT_REQUIRED")
    require(path.stat().st_size == %(size)d, "CREATION_RECEIPT_SIZE_CHANGED")
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == %(sha)r, "CREATION_RECEIPT_CHANGED")
    receipt = json.loads(data)
    observation = receipt.get("observation", {})
    require(receipt.get("status") == "ADMIN_RESOURCES_CREATED_AUTO_ENROLLMENT_NOT_DEPLOYED" and observation.get("viewer_share_verified") is True, "RESOURCE_CREATION_NOT_ACCEPTED")
    require(observation.get("knowledge_base_id") == %(kb)r and observation.get("organization_id") == %(org)r and observation.get("owner_tenant_id") == "10000", "CREATED_RESOURCES_CHANGED")
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


''' % {"path": evidence["report_path"], "size": evidence["report_bytes"], "sha": evidence["report_sha256"], "kb": evidence["knowledge_base_id"], "org": evidence["organization_id"]}
    host = substitute(host, "def execute(report):", validation + "def execute(report):")
    host = substitute(host, '    before = {name: query(', '    report["creation_receipt"] = check_creation_receipt()\n    before = {name: query(')
    host = substitute(host, "CHILD = None", "CHILD = " + repr(child))
    compile(child, "<viewer-child>", "exec")
    compile(host, "<viewer-host>", "exec")
    return host.encode(), {"candidate_policy_sha256": hashlib.sha256(policy.encode()).hexdigest(), "creation_receipt": evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw, evidence = build()
    with args.output.open("xb") as output:
        output.write(raw)
    print(json.dumps({"path": str(args.output), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "candidate_policy_sha256": evidence["candidate_policy_sha256"]}))


if __name__ == "__main__":
    main()
