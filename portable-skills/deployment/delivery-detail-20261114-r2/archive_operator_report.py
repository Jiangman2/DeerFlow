"""Archive the exact redacted diagnostic JSON without replacing prior evidence."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
source = Path('C:/Users/15285/Downloads/log(26).txt')
raw = source.read_bytes()
lines = [line for line in raw.splitlines(keepends=True)
         if line.startswith(b'{') and b'energy-delivery-detail-inspection.v2' in line]
assert len(lines) == 1
document = lines[0]
report = json.loads(document)
assert report['thread_id'] == '8e27d29f-730b-4a89-9681-36fdddc87887'
assert report['status'] == 'DELIVERY_SEQUENCE_OBSERVED_NO_CHANGE'
directory = ROOT / 'release/energy-delivery-detail-inspection-20261114-r2'
metadata = {'schema': 'energy-delivery-detail-operator-source.v2',
            'source_file': source.name, 'source_bytes': len(raw),
            'source_sha256': hashlib.sha256(raw).hexdigest(),
            'report_bytes': len(document), 'report_sha256': hashlib.sha256(document).hexdigest(),
            'report_exact_source_line_preserved': True,
            'operator_observed_at': report['observed_at'], 'credentials_exported': False}
for path, data in ((directory / 'OPERATOR-REPORT.json', document),
                   (directory / 'OPERATOR-SOURCE.json', (json.dumps(metadata, indent=2, sort_keys=True)+'\n').encode())):
    if path.exists():
        assert path.read_bytes() == data, 'EXISTING_ARCHIVE_DIFFERS'
    else:
        path.write_bytes(data)
print(json.dumps(metadata, sort_keys=True))
