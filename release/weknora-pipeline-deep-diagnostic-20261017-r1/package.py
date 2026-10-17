"""Build a new immutable single-file diagnostic delivery from reviewed inputs."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REVIEWED = HERE.parent/'mineru-provider-switch-20261014'/'enrichment_diagnostic.py'
NAME = 'weknora-pipeline-deep-diagnostic-20261017-r1.py'
CONSTANTS = {'CATEGORIES', 'TX', 'WAIT_CATEGORIES', 'DB_WAITS_SQL', 'SCHEMA_SQL', 'SAMPLE_SELECT'}
FUNCTIONS = {'span_sql', 'check_schema', 'clean_plan', 'numeric', 'clean_spans', 'sql_read', 'span_snapshot', 'database_waits'}


def projection():
    raw = REVIEWED.read_text(encoding='utf-8')
    lines = raw.splitlines(keepends=True)
    tree = ast.parse(raw)
    selected = []
    names = set()
    for i, node in enumerate(tree.body):
        end = getattr(node, 'end_lineno', tree.body[i+1].lineno-1 if i+1 < len(tree.body) else len(lines))
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in CONSTANTS:
                selected.append(''.join(lines[node.lineno-1:end])); names.add(name)
        elif isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(''.join(lines[node.lineno-1:end])); names.add(node.name)
    if names != CONSTANTS | FUNCTIONS:
        raise RuntimeError('span_projection_source_changed')
    code = '\n\n'.join(selected)+'\n'
    if sys.version_info >= (3,8): ast.parse(code, feature_version=(3,7))
    else: ast.parse(code)
    return code


def payload():
    source = (HERE/'diagnose.py').read_text(encoding='utf-8')
    needle = 'SPAN_HELPER = None  # Filled from reviewed source by package.py, not server files.'
    if source.count(needle) != 1:
        raise RuntimeError('delivery_insertion_changed')
    source = source.replace(needle, 'SPAN_HELPER = '+repr(projection()))
    if sys.version_info >= (3,8): ast.parse(source, feature_version=(3,7))
    else: ast.parse(source)
    return source.encode('utf-8')


def launcher(sha):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/'''+NAME+'''
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-pipeline-deep-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/observe.py"
test "$(sha256sum "$CONTROL/observe.py" | awk '{print $1}')" = '''+sha+'''
echo "observe_log=$CONTROL/observe.log"
set +e
python3 -I -B -u "$CONTROL/observe.py" --observe 2>&1 | tee "$CONTROL/observe.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "observe_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "observe_log=$CONTROL/observe.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        raise RuntimeError('new_delivery_directory_required')
    raw = payload(); sha = hashlib.sha256(raw).hexdigest()
    args.destination.mkdir(parents=True)
    (args.destination/NAME).write_bytes(raw)
    (args.destination/'RUN-DATAX01.txt').write_bytes(launcher(sha).encode('utf-8'))
    (args.destination/'RUNBOOK.txt').write_bytes((HERE/'RUNBOOK.txt').read_bytes())
    evidence = dict(script=NAME, script_sha256=sha, python37_grammar='PASS', production_execution='NOT_EXECUTED_LOCALLY',
                    reviewed_span_source_sha256=hashlib.sha256(REVIEWED.read_bytes()).hexdigest(),
                    sampling_seconds=300, expected_total_minutes='6-8 (depends on read latency)',
                    production_writes=0, provider_requests=0, ready_to_expand=False,
                    tests='SEE_SEPARATE_QUALIFICATION_RECEIPTS')
    (args.destination/'VERIFICATION.json').write_text(json.dumps(evidence, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(evidence))


if __name__ == '__main__':
    main()
