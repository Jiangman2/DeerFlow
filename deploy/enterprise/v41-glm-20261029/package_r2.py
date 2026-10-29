"""Package r2's scoped Kubernetes reader; retain r1 and the shared call claim."""
import argparse
import ast
import hashlib
import json
from pathlib import Path


def packaged():
    root = Path(__file__).resolve().parent
    child = (root / 'child.py').read_text(encoding='utf-8')
    require_sha = '0fc6e5192f7c8e4553ad6ba1077a464babf0589f250e1debe993ef42db24f02e'
    assert hashlib.sha256(child.encode()).hexdigest() == require_sha, 'PROVIDER_TEST_CHANGED'
    host = (root / 'host_r2.py').read_text(encoding='utf-8')
    host = host.replace('CHILD = None  # Replaced by package.py; the source template is not executable.', 'CHILD = ' + repr(child))
    host = host.replace('CHILD_SHA = None', 'CHILD_SHA = ' + repr(require_sha))
    ast.parse(host, feature_version=(3, 7)); ast.parse(child, feature_version=(3, 7))
    return host.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('output', type=Path); args = parser.parse_args()
    raw = packaged()
    with args.output.open('xb') as handle: handle.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'output': str(args.output)}))
