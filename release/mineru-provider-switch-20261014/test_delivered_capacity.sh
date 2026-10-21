#!/usr/bin/env bash
# Run only in the disposable no-network test container, hostname datax-01.
set -Eeuo pipefail
test "$(hostname -s)" = datax-01
test -n "${CAPACITY_FIXTURE_ONLY:-}"
useradd -m cnooc
TEST_ROOT=$(mktemp -d /tmp/capacity-delivery-XXXXXXXX)
mkdir "$TEST_ROOT/control" "$TEST_ROOT/bin"
tar -xzf /artifact/deerflow-mineru-capacity-20261014-r1.tar.gz -C "$TEST_ROOT/control"
install -m 0755 /qa/fixture_kubectl_capacity.py "$TEST_ROOT/bin/kubectl"
export PATH="$TEST_ROOT/bin:$PATH"
if ! bash "$TEST_ROOT/control/run-datax01-capacity.sh" > "$TEST_ROOT/output.log"; then
  # The fixture contains no real credentials; retain actionable test diagnostics.
  tail -n 20 "$TEST_ROOT/output.log"
  exit 1
fi
python -B - "$TEST_ROOT/output.log" <<'PY'
import hashlib, json, os, pwd, stat, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text()
assert 'FIXTURE-PRIVATE-VALUE' not in text
assert 'capacity_exit_code=0 tee_exit_code=0' in text
download = Path(next(x.split('=', 1)[1] for x in text.splitlines() if x.startswith('download_directory=')))
assert download.parent == Path('/home/cnooc')
assert stat.S_IMODE(download.stat().st_mode) == 0o700
assert download.stat().st_uid == pwd.getpwnam('cnooc').pw_uid
for name in ('capacity.log', 'RESULT.json'):
    p = download / name
    assert p.stat().st_uid == pwd.getpwnam('cnooc').pw_uid
    assert stat.S_IMODE(p.stat().st_mode) == 0o640
    assert 'FIXTURE-PRIVATE-VALUE' not in p.read_text()
data = json.loads((download / 'RESULT.json').read_text())
assert data['inventory'] == 'COMPLETE' and data['baseline_stable_during_audit'] is True
assert data['provider_requests'] == 0 and data['production_workloads_mutated'] is False
assert data['ready_for_rollout'] is False
assert len(data['deployments']) == 4 and len(data['nodes']) == 1
assert data['steady_state_slots_upper_bound_requests_only'] == 13
receipt = next(json.loads(x) for x in text.splitlines() if x.startswith('{') and '"report_sha256"' in x)
raw = (download / 'RESULT.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == receipt['report_sha256']
assert raw == Path(receipt['report']).read_bytes()
print(json.dumps({'delivered_capacity_package': 'PASS', 'real_cluster_access': False,
                  'credential_redaction': 'PASS', 'export_ownership_and_hash': 'PASS',
                  'fixture_nodes': 1, 'provider_requests': 0}))
PY
