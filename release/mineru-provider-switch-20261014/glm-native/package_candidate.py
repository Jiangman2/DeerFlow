#!/usr/bin/env python3
"""Package reviewed source delta and a separate read-only server inventory."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
VENDOR = REPO / 'vendor/weknora-app-20260912'
CODE = [
    'internal/models/vlm/vlm.go',
    'internal/models/vlm/enterprise_glm.go',
    'internal/models/vlm/enterprise_glm_config.go',
    'internal/models/vlm/enterprise_glm_test.go',
    'internal/models/vlm/enterprise_glm_stream_test.go',
    'internal/models/vlm/enterprise_glm_core_test.go',
    'cmd/glm-vlm-probe/main.go',
]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(argv, cwd):
    r = subprocess.run(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120, check=False)
    if r.returncode:
        raise RuntimeError('local verification failed: '+Path(argv[0]).name)
    return r.stdout


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--go', type=Path, required=True)
    args = p.parse_args()
    target = args.output.resolve()
    if target.exists():
        raise RuntimeError('refuse to overwrite existing delivery')
    go = str(args.go.resolve())
    core = ['enterprise_glm.go', 'enterprise_glm_stream_test.go', 'enterprise_glm_core_test.go']
    go_result = checked([go, 'test', '-count=1', '-json']+core, VENDOR/'internal/models/vlm')
    checked([go, 'vet']+core, VENDOR/'internal/models/vlm')
    audit_result = checked([sys.executable, '-B', '-m', 'unittest', '-v', 'test_audit_build_inputs'], HERE)
    events = [json.loads(line) for line in go_result.decode().splitlines()]
    passes = [e['Test'] for e in events if e.get('Action') == 'pass' and 'Test' in e]
    for name in ('audit_build_inputs.py',):
        ast.parse((HERE/name).read_text(encoding='utf-8'), feature_version=(3, 7))
    payload = {name: (VENDOR/name).read_bytes() for name in CODE}
    for name in ('go.mod', 'go.sum', 'third_party/anydoc-go/Cargo.lock'):
        # Record only hashes of build context inputs, not an incomplete module
        # tree masquerading as a buildable source package.
        assert (VENDOR/name).is_file()
    verification = {
        'delivery_kind': 'SOURCE_DELTA_NOT_BUILD_CONTEXT_OR_ROLLOUT_TOOL',
        'production_writes': 0, 'provider_requests': 0, 'credentials_included': False,
        'go_core_tests': 'PASS', 'go_core_test_events_including_subtests': len(passes),
        'go_core_top_level_tests': len([x for x in passes if '/' not in x]),
        'core_go_vet': 'PASS', 'inventory_unittests': '6_PASS', 'inventory_python37_grammar': 'PASS',
        'full_vlm_tests': 'BLOCKED_WINDOWS_CGO_PG_QUERY_PARSE_DEPARSE',
        'linux_arm64_server_anydoc_build': 'NOT_RUN', 'native_glm_intranet_probe': 'NOT_RUN',
        'native_enrichment_end_to_end': 'NOT_RUN', 'candidate_image_built': False,
        'ready_for_rollout': False, 'database_schema_migration': False,
        'source_sha256': {n: sha(v) for n, v in payload.items()},
        'dependency_sha256': {n: sha((VENDOR/n).read_bytes()) for n in ('go.mod', 'go.sum', 'third_party/anydoc-go/Cargo.lock')},
        'python_wire_receipt_sha256': 'be1086b812a8e07e1375eccc42cb0e08022666b1dbc38b6e043c2eca1ac0c07d',
        'old88_continue_permitted': False,
    }
    target.mkdir(parents=True, mode=0o700)
    (target/'LOCAL-VERIFICATION.json').write_text(json.dumps(verification, indent=2)+'\n', encoding='utf-8')
    (target/'RUNBOOK.txt').write_bytes((HERE/'RUNBOOK.txt').read_bytes())
    script_name = 'weknora-glm-build-inputs-20261016-r1.py'
    (target/script_name).write_bytes((HERE/'audit_build_inputs.py').read_bytes())
    (target/'core-tests.jsonl').write_bytes(go_result)
    (target/'inventory-tests.txt').write_bytes(audit_result)
    with tarfile.open(str(target/'weknora-glm-native-source-delta-20261016-r1.tar.gz'), 'w:gz') as tar:
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data); info.mode = 0o644; info.uid = info.gid = 0; info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    members = sorted(x for x in target.iterdir() if x.is_file())
    (target/'SHA256SUMS.txt').write_text(''.join(sha(x.read_bytes())+'  '+x.name+'\n' for x in members), encoding='ascii')
    print(json.dumps({'delivery': str(target), 'audit_script_sha256': sha((target/script_name).read_bytes()),
                      'local_verification': verification}, ensure_ascii=False))


if __name__ == '__main__':
    main()
