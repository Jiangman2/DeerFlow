"""Record root's independent DB helper review and exact frozen-asset checks."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/native-db-design-r2')


def ref(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


candidate = HERE / 'db_stage.py'
assert ref(candidate)['sha256'] == 'cad8fe569e2461b3ce57e508c9ef832aea4479d1ec61bb260a5b8f45f1a0a398'
ast.parse(candidate.read_bytes(), feature_version=(3,7))
spec = importlib.util.spec_from_file_location('db_root_review', candidate)
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
for name in ('POD-TEMPLATE.json', 'probe_supervisor.py', 'bootstrap_probe.py', 'RECEIPT-CONTRACT.json'):
    assert helper.asset_bytes(name) == (SOURCE / name).read_bytes(), name
owner = '1' * 32
profile = dict(schema='portal-native-pg-profile.v1', owner=owner, uid=1234, gid=1235,
               server_binary_version='16.15', database_started=False, **helper.PG_FILES)
pod = helper.render_db(owner, 'portal-ppt-' + owner, profile)
pg = pod['spec']['initContainers'][0]
assert pg['args'][:4] == ['-i', 'PATH=' + helper.PG_PATH, 'HOME=/tmp', 'LANG=C.UTF-8']
assert pg['securityContext']['runAsUser'] == 1234 and pg['securityContext']['runAsGroup'] == 1235
assert pg['image'] == helper.PG_CACHE['reference'] and pg['imagePullPolicy'] == 'Never'
assert 'PGDATA=/var/lib/postgresql/data/pgdata' in pg['args']
assert pg['restartPolicy'] == 'Always' and pod['spec']['securityContext']['fsGroup'] == 65532
qualification = json.loads((HERE / 'DB-QUALIFICATION.json').read_bytes())
for name, pin in qualification['files'].items():
    actual = ref(HERE / name)
    assert (actual['bytes'],actual['sha256']) == (pin['bytes'],pin['sha256'])
review = dict(schema='portal-native-db-stage-independent-root-review.v1', status='PASS',
    reviewer='/root', blockers=[], files={name:ref(HERE/name) for name in ('db_stage.py','db_stage_template.py','bind_db_stage.py','test_db_stage.py','DB-QUALIFICATION.json')},
    frozen_assets_byte_exact=True, business_probes_rewritten=False,
    static_checks=[
        'Only original cached PG index reference used with Never; explicit selected-manifest/config mapping remains bound',
        'PG identity/path/version probe uses fixed image-local reads as UID65532, cleared environment and read-only root without DB startup',
        'Disposable PG uses actual probed UID/GID, supplementary fsGroup and owned PGDATA subdirectory in memory volumes',
        'PG environment is explicit including C.UTF-8; no inherited image environment or production credentials',
        'Only fixed five existing business stages and final receipt collector are rendered',
        'Summary validation delegates exact existing receipt contract; wrong revisions, sources, subprocess exits and retirement are rejected',
        'No Kubernetes client or production/model operation is present in the pure helper'],
    author_tests_reviewed=dict(original_passed=7, delta_passed=1, unique_tests=7, broad_suites_repeated=False),
    root_business_or_server_execution=False, ready_for_rollout=False)
raw = (json.dumps(review, sort_keys=True, indent=2) + '\n').encode()
with (HERE / 'ROOT-DB-STAGE-REVIEW.json').open('xb') as stream: stream.write(raw)
print(json.dumps(dict(status='PASS', helper_sha256=ref(candidate)['sha256'], frozen_assets=4, review_sha256=hashlib.sha256(raw).hexdigest())))
