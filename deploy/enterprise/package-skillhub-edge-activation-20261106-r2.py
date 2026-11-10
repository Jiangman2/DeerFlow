"""Build the one-file edge tool; by default remains unbound and cannot act."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'deploy/enterprise/skillhub_edge_activation_20261106_r2.py'
POLICY = ROOT / 'deploy/enterprise/skillhub_nginx_auth_transform_20261106_r2.py'
NAME = 'activate-portal-skillhub-edge-auth-20261106-r2.py'


def generate(pin=None):
    source = SOURCE.read_text(encoding='utf-8')
    policy = POLICY.read_text(encoding='utf-8')
    source = source.replace('import skillhub_nginx_auth_transform_20261106_r2 as policy',
        'import types\npolicy = types.ModuleType("skillhub_edge_policy")\nexec(' + repr(policy) + ', policy.__dict__)')
    if pin is not None:
        sys.path.insert(0, str(SOURCE.parent))
        from skillhub_edge_activation_20261106_r2 import validate_pin
        validate_pin(pin)
        source = source.replace('NATIVE_RECEIPT_BINDING = None  # Bound only after actual native rollout acceptance.',
                                'NATIVE_RECEIPT_BINDING = ' + repr(pin))
    ast.parse(source, feature_version=(3, 7))
    return source.encode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory', required=True)
    parser.add_argument('--native-receipt-binding', help='Reviewed operator receipt {bytes,sha256,operation}; omit for fail-closed draft.')
    args = parser.parse_args()
    pin = json.loads(Path(args.native_receipt_binding).read_text(encoding='utf-8')) if args.native_receipt_binding else None
    raw = generate(pin)
    directory = Path(args.output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / NAME
    # Do not silently replace a previously prepared operator tool.
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'path': str(target), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'native_receipt_bound': pin is not None, 'production_commands_executed': False}, sort_keys=True))


if __name__ == '__main__':
    main()
