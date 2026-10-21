"""Prepare one reviewable shell artifact; never execute its embedded tool."""
import ast
import base64
import gzip
import hashlib
from pathlib import Path

HERE = Path(__file__).absolute().parent
NAME = 'run-portal-ppt-host-base-20261017-r1.sh'


def render():
    source = (HERE / 'wrapper_bootstrap.py').read_text(encoding='utf-8')
    tree = ast.parse(source, feature_version=(3, 7))
    rows = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'EXPECTED_FILES' for target in node.targets)]
    assert len(rows) == 1 and set(rows[0]) == {'verify_host_base.py', 'EXPECTATIONS.json',
                                            'frozen_gateway_replay.py', 'frozen_export_verifier.py'}
    payload = {}
    for name, (expected, size) in rows[0].items():
        raw = (HERE / name).read_bytes()
        assert len(raw) == size and hashlib.sha256(raw).hexdigest() == expected, name
        payload[name] = base64.b64encode(gzip.compress(raw, mtime=0)).decode('ascii')
    marker = 'PAYLOAD = {}  # Generator replaces this exact assignment with four locked blobs.'
    assert source.count(marker) == 1
    source = source.replace(marker, 'PAYLOAD = ' + repr(payload))
    shell = '#!/usr/bin/env bash\nset -eu\n'
    shell += '[ "$#" -eq 0 ] || { echo "This fixed diagnostic accepts no arguments." >&2; exit 2; }\n'
    shell += "exec /usr/bin/python3 -I -B -u - <<'PORTAL_HOST_BASE_BOOTSTRAP_PY'\n"
    shell += source + '\nPORTAL_HOST_BASE_BOOTSTRAP_PY\n'
    return shell.encode('utf-8')


if __name__ == '__main__':
    raw = render()
    with (HERE / NAME).open('xb') as stream:
        stream.write(raw)
    print('%s  %s  bytes=%d' % (hashlib.sha256(raw).hexdigest(), NAME, len(raw)))
