"""Seal the qualified member UI asset patch into a Python 3.7 host release tool."""
import argparse
import ast
import base64
import hashlib
import json
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def build(payload_dir, installed_dir, output):
    files = {p.name: p.read_bytes().decode('utf-8').replace('\r\n', '\n') for p in payload_dir.iterdir() if p.is_file()}
    files['install.sh'] = (HERE / 'install-weknora-member-assets.sh').read_text(encoding='utf-8').replace('\r\n', '\n')
    baseline = {}
    for line in files['baseline.sha256'].splitlines():
        sha, name = line.split()
        baseline[name] = sha
    final = {}
    for name in baseline:
        new = name if name == 'index.html' else name[:-3] + '.members-r1.js'
        final[new] = hashlib.sha256((installed_dir / new).read_bytes()).hexdigest()
    hashes = dict(final)
    hashes.update({k:v for k,v in baseline.items() if k != 'index.html'})
    files['final.sha256'] = ''.join(sha + '  ' + name + '\n' for name, sha in sorted(hashes.items()))
    expected = {'status': 'INSTALLED_MEMBER_UI_ACCEPTED', 'old_modules_preserved': True,
                'new_module_graph_verified': True, 'production_api_calls': 0, 'membership_writes': 0}
    files['verify.sh'] = '''set -eu
cd /usr/share/nginx/html
sha256sum -c /opt/weknora-member-fix/final.sha256 >/dev/null
test -s weknora-members-fix-r1.json
! grep -q '\\.legacy-office-r6\\.js' index.html
count=0
for file in assets/*.legacy-office-r6.members-r1.js; do
  test -s "$file"
  ! grep -q '\\.legacy-office-r6\\.js' "$file"
  count=$((count + 1))
done
test "$count" -ge 40 && test "$count" -le 250
printf '%s\\n' ''' + "'" + json.dumps(expected, separators=(',', ':')) + "'\n"
    payload = {'files': files, 'payload_sha256': digest(files), 'baseline_hashes': baseline,
               'final_hashes': final, 'job_expected': expected}
    source = (HERE / 'activate-weknora-member-ui.py').read_text(encoding='utf-8')
    assert source.count('__SEALED_PAYLOAD__') == 1
    encoded = base64.b64encode(zlib.compress(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode(), 9)).decode()
    source = source.replace('__SEALED_PAYLOAD__', encoded)
    ast.parse(source, feature_version=(3, 7))
    output.write_bytes(source.encode('utf-8'))
    record = {'schema': 'portal-weknora-member-ui-package.v1', 'output': str(output),
        'bytes': output.stat().st_size, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'payload_sha256': payload['payload_sha256'], 'baseline_hashes': baseline, 'final_hashes': final,
        'production_deployed': False, 'installed_candidate_acceptance_pending': True}
    output.with_suffix('.package.json').write_bytes((json.dumps(record, indent=2) + '\n').encode())
    (payload_dir / 'final.sha256').write_bytes(files['final.sha256'].encode())
    (payload_dir / 'verify.sh').write_bytes(files['verify.sh'].encode())
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload-dir', type=Path, required=True)
    parser.add_argument('--installed-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.payload_dir, args.installed_dir, args.output)))
