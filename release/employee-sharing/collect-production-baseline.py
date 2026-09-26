#!/usr/bin/env python3
"""Read-only production source audit. Host Python 3.7+, no dependencies.

No deployment, database, files inside Pods, or application data are modified.
The only write is the explicitly requested local, mode-0600 evidence file.
Never collects environment values, config.yaml, .env, Secrets, prompts, or docs.
"""
import argparse
import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys

BACKEND = [
    "pyproject.toml", "packages/harness/pyproject.toml",
    "app/gateway/app.py", "app/gateway/deps.py", "app/gateway/services.py",
    "app/gateway/routers/agents.py", "app/gateway/routers/thread_runs.py",
    "app/gateway/routers/threads.py", "app/gateway/routers/scheduled_tasks.py",
    "app/gateway/routers/weknora.py", "app/gateway/weknora_identity.py",
    "app/gateway/auth/models.py", "app/channels/manager.py",
] + ["packages/harness/deerflow/" + name for name in [
    "agents/lead_agent/agent.py", "agents/lead_agent/prompt.py",
    "agents/middlewares/skill_activation_middleware.py",
    "agents/middlewares/skill_tool_policy_middleware.py",
    "agents/middlewares/skill_workflow_middleware.py",
    "agents/middlewares/weknora_scope_middleware.py",
    "config/agents_config.py", "config/app_config.py", "config/paths.py",
    "config/reload_boundary.py", "config/memory_config.py", "config/skills_config.py",
    "persistence/agents/__init__.py", "persistence/agents/base.py",
    "persistence/agents/file.py", "persistence/agents/sql.py", "persistence/agents/model.py",
    "runtime/runs/worker.py", "runtime/runs/manager.py", "runtime/context.py",
    "runtime/user_context.py", "runtime/weknora_context.py",
    "sandbox/sandbox_provider.py", "sandbox/local/local_sandbox_provider.py", "sandbox/tools.py",
    "skills/storage/__init__.py", "skills/storage/local_skill_storage.py",
    "skills/storage/user_scoped_skill_storage.py", "skills/storage/skill_storage.py",
    "skills/types.py", "skills/parser.py", "skills/skillscan.py",
    "subagents/executor.py", "tools/tools.py", "tools/builtins/setup_agent_tool.py",
    "tools/builtins/update_agent_tool.py", "tools/builtins/task_tool.py",
    "community/aio_sandbox/aio_sandbox_provider.py", "community/aio_sandbox/backend.py",
    "community/aio_sandbox/remote_backend.py", "community/aio_sandbox/local_backend.py",
]]
FRONTEND = ["package.json", "pnpm-lock.yaml", "tsconfig.json"] + ["src/" + name for name in [
    "core/agents/api.ts", "core/agents/hooks.ts", "core/agents/types.ts",
    "core/agents/routing.ts", "core/agents/index.ts", "core/agents/shared-agents.ts",
    "core/threads/types.ts", "core/threads/hooks.ts", "core/threads/api.ts",
    "core/i18n/locales/types.ts", "core/i18n/locales/en-US.ts", "core/i18n/locales/zh-CN.ts",
    "components/workspace/agents/agent-card.tsx", "components/workspace/agents/agent-gallery.tsx",
    "components/workspace/agents/digital-employee-settings-dialog.tsx",
    "components/workspace/agents/agent-bootstrap-dialog.tsx",
    "components/workspace/chats/chat-panel.tsx",
    "app/workspace/agents/page.tsx", "app/workspace/agents/new/page.tsx",
    "app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
]]

# Supplemental coverage only. Existing anchors attest the same deployed images;
# missing new modules are explicit absence evidence, not a guessed empty tree.
SUPPLEMENT_BACKEND = [
    "app/gateway/app.py", "app/gateway/agent_knowledge.py",
    "app/gateway/agent_publications.py", "app/gateway/employee_knowledge.py",
    "app/gateway/employee_publication.py", "app/gateway/routers/employee_resources.py",
    "app/gateway/routers/employee_changes.py",
] + ["packages/harness/deerflow/" + name for name in [
    "runtime/context_compaction.py", "runtime/employee_resources.py",
    "agents/middlewares/employee_publication_middleware.py",
    "config/agent_knowledge_config.py", "skills/agent_publication.py",
    "persistence/agents/avatars.py", "persistence/agents/definition_backup.py",
    "persistence/agents/deletion.py", "persistence/agents/drafts.py",
    "persistence/agents/inventory.py", "persistence/agents/proposals.py",
    "persistence/agents/publications.py", "persistence/agents/resource_grants.py",
    "persistence/agents/runtime.py",
]]
SUPPLEMENT_FRONTEND = ["package.json"] + ["src/" + name for name in [
    "components/workspace/input-box.tsx", "components/workspace/input-box-helpers.ts",
    "core/skills/api.ts", "core/skills/hooks.ts", "core/skills/type.ts",
    "core/skills/invocation.ts", "core/skills/invocation-hooks.ts",
    "core/agents/resources.ts", "core/agents/skill-selection.ts",
    "core/agents/knowledge-selection.ts", "core/agents/feature-cache.ts",
    "components/workspace/agents/agent-skill-selection.tsx",
    "components/workspace/agents/agent-knowledge-selection.tsx",
    "components/workspace/agents/agent-settings-dialog-helpers.ts",
    "components/workspace/agents/employee-delete-dialog.tsx",
    "components/workspace/agents/employee-change-review.tsx",
    "components/workspace/agents/employee-versions.tsx",
    "components/workspace/agents/employee-version-notice.tsx",
    "components/workspace/agents/publication-review-dialog.tsx",
    "components/workspace/agents/resource-grant-detail.tsx",
    "app/workspace/agents/grants/[id]/page.tsx",
    "app/workspace/agents/manage/[name]/page.tsx",
    "app/workspace/agents/manage/[name]/proposals/[id]/page.tsx",
]]

# Direct dependency contracts discovered while composing on the real baseline.
# This is a separate, small profile: no repeat export of the previous 81 files,
# user data, installed Skills, environment values or live database contents.
DEPENDENCY_BACKEND = [
    "app/gateway/app.py", "app/gateway/authz.py", "app/gateway/routers/__init__.py",
] + ["packages/harness/deerflow/" + name for name in [
    "skills/agent_selection.py", "skills/permissions.py",
    "skills/skillscan/__init__.py", "skills/skillscan/models.py",
    "skills/skillscan/orchestrator.py", "skills/package_files.py",
    "skills/package_paths.py", "tools/types.py", "constants.py", "config/__init__.py",
]]
DEPENDENCY_FRONTEND = ["package.json"] + ["src/" + name for name in [
    "core/threads/utils.ts", "components/workspace/knowledge-scope-selector.tsx",
    "core/auth/AuthProvider.tsx", "components/ui/alert.tsx",
    "components/ui/dialog.tsx", "components/ui/toggle-group.tsx",
]]

# Each reader is a fixed allowlist, not a directory/archive or environment dump.
PYTHON_READER = r'''
import base64, hashlib, json, os, sys
root, paths = sys.argv[1], json.loads(sys.argv[2])
out = {}
for relative in paths:
    path = os.path.join(root, relative)
    cursor = root
    for part in relative.split("/"):
        cursor = os.path.join(cursor, part)
        if os.path.islink(cursor):
            raise RuntimeError("Linked source path: " + relative)
    if not os.path.lexists(path):
        out[relative] = {"missing": True}; continue
    if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) > 2097152:
        raise RuntimeError("Unsafe or oversized allowlisted source: " + relative)
    with open(path, "rb") as stream:
        data = stream.read(2097153)
    if len(data) > 2097152:
        raise RuntimeError("Source changed size during audit")
    out[relative] = {"sha256": hashlib.sha256(data).hexdigest(), "base64": base64.b64encode(data).decode("ascii")}
print(json.dumps({"files": out, "python": sys.version.split()[0]}))
'''
NODE_READER = r'''
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const root = process.argv[1], paths = JSON.parse(process.argv[2]), out = {};
for (const relative of paths) {
  const p = path.join(root, relative);
  let cursor = root;
  for (const part of relative.split('/')) {
    cursor = path.join(cursor, part);
    try {
      if (fs.lstatSync(cursor).isSymbolicLink()) throw Error('Linked source: ' + relative);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  }
  if (!fs.existsSync(p)) { out[relative] = {missing: true}; continue; }
  const stat = fs.lstatSync(p);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 2097152) throw Error('Unsafe source: ' + relative);
  const data = fs.readFileSync(p);
  if (data.length > 2097152) throw Error('Oversized source: ' + relative);
  out[relative] = {sha256: crypto.createHash('sha256').update(data).digest('hex'), base64: data.toString('base64')};
}
console.log(JSON.stringify({files: out, node: process.version}));
'''


def kubectl(arguments, timeout=45):
    env = dict(os.environ, KUBECONFIG="/etc/kubernetes/admin.conf")
    result = subprocess.run(["kubectl", "--request-timeout=20s", "-n", "deer-flow"] + arguments,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout)
    if result.returncode:
        # Raw errors may contain URLs or configuration; keep them on the host
        # out of the report rather than echoing unrestricted command output.
        raise RuntimeError("kubectl read failed (exit %s)" % result.returncode)
    if len(result.stdout) > 64 * 1024 * 1024:
        raise RuntimeError("Audit response exceeds limit")
    return json.loads(result.stdout)


def collect_component(deployment, container, root, paths, frontend=False):
    before = kubectl(["get", "deployment", deployment, "-o", "json"])
    spec = before["spec"]["template"]["spec"]
    desired = next(c for c in spec["containers"] if c["name"] == container)
    pods = kubectl(["get", "pods", "-l", "app=" + deployment, "-o", "json"])["items"]
    candidates = []
    for pod in pods:
        if pod["metadata"].get("deletionTimestamp") or pod.get("status", {}).get("phase") != "Running":
            continue
        for status in pod["status"].get("containerStatuses", []):
            if status["name"] == container and status.get("ready") and status["image"] == desired["image"]:
                candidates.append((pod, status))
    if not candidates:
        raise RuntimeError("No Ready Pod for exact deployed image")
    pod, status = candidates[0]
    reader = ["node", "-e", NODE_READER] if frontend else ["/app/backend/.venv/bin/python" if deployment == "gateway" else "python3", "-c", PYTHON_READER]
    source = kubectl(["exec", pod["metadata"]["name"], "-c", container, "--"] + reader + [root, json.dumps(paths)], timeout=60)
    after = kubectl(["get", "deployment", deployment, "-o", "json"])
    if before["metadata"]["resourceVersion"] != after["metadata"]["resourceVersion"]:
        raise RuntimeError("Deployment changed during audit; rerun without concurrent rollout")
    after_pod = kubectl(["get", "pod", pod["metadata"]["name"], "-o", "json"])
    after_status = next((item for item in after_pod.get("status", {}).get("containerStatuses", []) if item.get("name") == container), None)
    # Deployment resourceVersion alone misses container restarts and mutable-tag
    # image changes. The source and reported digest must belong to one instance.
    if (not pod["metadata"].get("uid") or not status.get("containerID")
            or after_pod.get("metadata", {}).get("uid") != pod["metadata"]["uid"]
            or after_pod.get("metadata", {}).get("deletionTimestamp")
            or after_pod.get("status", {}).get("phase") != "Running"
            or not after_status or not after_status.get("ready")
            or any(after_status.get(key) != status.get(key) for key in ("image", "imageID", "containerID", "restartCount"))):
        raise RuntimeError("Pod changed during audit; rerun after the instance is stable")
    files = source.get("files", {})
    missing = sorted(path for path in paths if path not in files or files[path].get("missing", False))
    collected = sum(1 for path in paths if files.get(path, {}).get("sha256") and files[path].get("base64") is not None)
    if not collected:
        raise RuntimeError("No allowlisted source files found; verify source root")
    return {"deployment": deployment, "container": container, "image": desired["image"],
            "image_id": status.get("imageID"), "pod": pod["metadata"]["name"], "node": pod["spec"].get("nodeName"),
            "replicas": before["spec"].get("replicas"), "generation": before["metadata"]["generation"],
            "environment_names_only": sorted(e["name"] for e in desired.get("env", [])),
            "source_root": root, "source_coverage": "PARTIAL" if missing else "COMPLETE",
            "source_files_collected": collected, "source_paths_missing": missing, "files": files,
            "runtime": {key: source[key] for key in ("python", "node") if key in source}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", choices=("baseline", "supplemental", "dependencies"), default="baseline")
    args = parser.parse_args()
    if socket.gethostname().split(".")[0] != "datax-01":
        raise SystemExit("Run only on datax-01")
    if os.geteuid() != 0:
        raise SystemExit("Use sudo; admin.conf remains root-readable only")
    output = os.path.abspath(args.output)
    if not output.endswith(".json") or os.path.lexists(output):
        raise SystemExit("Choose a new .json evidence path; existing files are never overwritten")
    supplemental = args.profile != "baseline"
    backend_paths, frontend_paths = {
        "baseline": (BACKEND, FRONTEND),
        "supplemental": (SUPPLEMENT_BACKEND, SUPPLEMENT_FRONTEND),
        "dependencies": (DEPENDENCY_BACKEND, DEPENDENCY_FRONTEND),
    }[args.profile]
    document = {"audit": "employee-sharing-source-supplement-v1" if supplemental else "employee-sharing-source-baseline-v1", "utc": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                "profile": args.profile, "production_mutation": "NONE", "secrets_collected": False, "components": {}, "errors": []}
    for component, container, root, paths, frontend in [
        ("gateway", "gateway", "/app/backend", backend_paths, False),
        ("frontend", "frontend", "/app/frontend", frontend_paths, True),
        ("provisioner", "provisioner", "/app", ["app.py", "requirements.txt"], False),
    ]:
        print("source_audit=START component=" + component, flush=True)
        try:
            document["components"][component] = collect_component(component, container, root, paths, frontend)
            record = document["components"][component]
            print("source_audit=PASS component=" + component + " coverage=" + record["source_coverage"]
                  + " files=" + str(record["source_files_collected"])
                  + " missing=" + str(len(record["source_paths_missing"])), flush=True)
        except (RuntimeError, ValueError, KeyError, StopIteration, subprocess.TimeoutExpired, OSError) as exc:
            document["errors"].append({"component": component, "type": type(exc).__name__})
            print("source_audit=FAIL component=" + component + " type=" + type(exc).__name__, flush=True)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    print("audit_file=" + output)
    print("audit_sha256=" + hashlib.sha256(payload).hexdigest())
    print("production_workloads_mutated=no")
    return 1 if document["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
