#!/usr/bin/env bash
set -Eeuo pipefail
test "$(hostname -s)" = datax-01
test "$(id -u)" -eq 0
export KUBECONFIG=/etc/kubernetes/admin.conf
unset SSL_CERT_FILE REQUESTS_CA_BUNDLE CURL_CA_BUNDLE
NS=deer-flow

ready_pod() {
  kubectl -n "${NS}" get pods -l "$1" -o json | python3 -c '
import json, sys
doc=json.load(sys.stdin)
rows=[]
for item in doc.get("items", []):
    conditions={row.get("type"):row.get("status") for row in item.get("status",{}).get("conditions") or []}
    if not item.get("metadata",{}).get("deletionTimestamp") and item.get("status",{}).get("phase")=="Running" and conditions.get("Ready")=="True":
        rows.append(item["metadata"]["name"])
if not rows: raise SystemExit(1)
print(sorted(rows)[0])
'
}

echo "===== PPT RUNNER EXECUTION LEASE FD STATUS ====="
date
kubectl -n "${NS}" get deployment gateway provisioner ppt-master-runner -o wide
kubectl -n "${NS}" get pods -l 'app in (gateway,provisioner,ppt-master-runner)' -o wide
kubectl -n "${NS}" get endpointslice -l kubernetes.io/service-name=ppt-master-runner -o wide

RUNNER_POD="$(ready_pod app=ppt-master-runner)"
kubectl -n "${NS}" exec -i "${RUNNER_POD}" -c ppt-master-runner -- \
  /app/backend/.venv/bin/python - <<'PY'
import ast
import fcntl
import inspect
import subprocess
import sys
import uuid
from pathlib import Path
from deerflow.community.aio_sandbox import aio_sandbox_provider as module
from deerflow.config.paths import get_paths

source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
tree = ast.parse(source)
node = next(row for row in tree.body if isinstance(row, ast.FunctionDef) and row.name == "_open_lock_file")
modes=[]
for child_node in ast.walk(node):
    if isinstance(child_node, ast.Call) and isinstance(child_node.func, ast.Name) and child_node.func.id == "open" and len(child_node.args) >= 2:
        value=child_node.args[1]
        modes.append(value.s if isinstance(value, ast.Str) else value.value)
assert modes == ["a+"], modes
print("live_runner_execution_lease_fd=PASS mode=a+")

lease_dir=get_paths().base_dir / ".sandbox-execution-leases"
lease_dir.mkdir(parents=True, exist_ok=True)
path=lease_dir / (".fd-status-probe-%s.lock" % uuid.uuid4().hex)
child='''import fcntl,sys\nf=open(sys.argv[1],"a+")\ntry:\n fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:\n sys.exit(42)\nfinally:\n f.close()\nsys.exit(0)'''
try:
    handle=open(path,"a+",encoding="utf-8")
    try:
        fcntl.flock(handle,fcntl.LOCK_SH)
        assert subprocess.run([sys.executable,"-c",child,str(path)],check=False).returncode == 42
        fcntl.flock(handle,fcntl.LOCK_UN)
    finally:
        handle.close()
    assert subprocess.run([sys.executable,"-c",child,str(path)],check=False).returncode == 0
finally:
    try: path.unlink()
    except FileNotFoundError: pass
print("execution_lease_nfs_cross_process_flock=PASS")
print("execution_lease_probe_cleanup=PASS")
PY

NGINX_POD="$(ready_pod app=nginx)"
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://ppt-master-runner:8003/health
echo
kubectl -n "${NS}" exec "${NGINX_POD}" -c nginx -- wget -qO- -T 10 http://gateway:8001/health
echo

if kubectl -n "${NS}" logs "${RUNNER_POD}" -c ppt-master-runner --since=30m --tail=2000 2>&1 | grep -F 'Bad file descriptor'; then
  echo "recent_bad_file_descriptor=FAIL"
  exit 1
fi
echo "recent_bad_file_descriptor=NONE"
echo "ppt_runner_execution_lease_fd_status=PASS"
