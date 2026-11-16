import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASE = HERE.parents[2] / 'release/energy-html-reader-preflight-20261114-r1'


def build():
    helper = (HERE / 'nginx_reader.py').read_text(encoding='utf-8')
    source = (HERE / 'preflight.py').read_text(encoding='utf-8')
    assert source.count("READER_SOURCE = ''") == 1
    source = source.replace("READER_SOURCE = ''", 'READER_SOURCE = ' + repr(helper))
    compile(source, '<sealed-reader-preflight>', 'exec')
    ast.parse(source, feature_version=(3, 7))
    ast.parse(helper, feature_version=(3, 7))
    return source.encode('utf-8')


def main():
    raw = build()
    filename = 'inspect-energy-html-reader-20261114-r1.py'
    meta = {'schema': 'energy-html-reader-preflight-package.v1', 'filename': filename,
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'read_only': True, 'production_executed': False, 'messages_sent': 0, 'provider_requests': 0}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in [(filename, raw), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())]:
        path = RELEASE / name
        if path.exists():
            assert path.read_bytes() == value, 'EXISTING_RELEASE_DIFFERS'
        else:
            path.write_bytes(value)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':
    main()
