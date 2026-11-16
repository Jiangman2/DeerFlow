"""Read-only differences for the published 2.1.5 Skill, across up to 3 nodes."""
import ast
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RELEASE = ROOT / 'release/energy-daily-reader-visibility-20261114-r1'
INSTALL = ROOT / 'release/energy-daily-html-reader-skill-20261114-r1'
TREE = '67b0cc44e9aec87fa4fa12274704f59baa636ccd566e55d8cb79cad23efbfeaa'
OPERATION = '8ca2af7963444e528d5add470bcf608e'
RECEIPT_SHA = 'e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb'
RUNTIME_SHA = 'cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def inventory():
    raw = (INSTALL / 'energy-market-daily-2.1.5-public-code.zip').read_bytes()
    assert sha(raw) == '37423629d9d5f334258168828efed44f890c34493995834054ee5a1425ab9d9d'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = {name.split('/', 1)[1]: archive.read(name) for name in archive.namelist()}
    binding = canonical({'schema': 'energy-browser-runtime-binding.v1',
                         'directory': '../.energy-browser-runtime-20261112-r1/linux-arm64',
                         'manifest_sha256': RUNTIME_SHA}) + b'\n'
    files['assets/browser-runtime.json'] = binding
    manifest = json.loads(files['MANIFEST.json'])
    manifest['files']['assets/browser-runtime.json'] = {'bytes': len(binding), 'sha256': sha(binding)}
    files['MANIFEST.json'] = canonical(manifest) + b'\n'
    rows = [{'path': name, 'bytes': len(value), 'sha256': sha(value)} for name, value in sorted(files.items())]
    assert len(rows) == 108 and sha(canonical(rows)) == TREE, 'EXPECTED_PRODUCTION_INVENTORY_MISMATCH'
    return rows


MULTI_VIEW = '''
def inspect_views(self):
    listing=self.query(['get','pods','-l','app.kubernetes.io/component=sandbox','-o','json'])
    need(isinstance(listing.get('items'),list) and len(listing['items'])<=4096,'POD_LIST_BOUND')
    candidates=[x for x in (ready_candidate(p) for p in listing['items']) if x]
    eligible=[x for x in candidates if x['mount_contract']]
    out={'ready_ordinary_pods':len(candidates),'eligible_public_mount_pods':len(eligible),
         'ineligible_public_mount_pods':len(candidates)-len(eligible),'read_only':True,'sampled_views':[]}
    choices=sorted(eligible,key=lambda x:(x['pod']!='sandbox-0913cd3d',x['node']!='ecs-zdz-pro-0017',x['pod']))
    selected=[];nodes=set()
    for candidate in choices:
        if candidate['node'] not in nodes:
            selected.append(candidate);nodes.add(candidate['node'])
        if len(selected)==3:break
    for candidate in selected:
        self.selected=candidate
        before=ready_candidate(self.query(['get','pod',candidate['pod'],'-o','json']))
        need(before==candidate,'POD_CHANGED_BEFORE_READ')
        result=self.query(['exec',candidate['pod'],'-c','sandbox','--','python3','-I','-B','-c',self.child,TREE])
        after=ready_candidate(self.query(['get','pod',candidate['pod'],'-o','json']))
        need(after==before,'POD_CHANGED_DURING_READ')
        need(isinstance(result,dict),'CHILD_RESPONSE_INVALID')
        out['sampled_views'].append({'sample':{k:before[k] for k in ('pod','uid','node','mount_readonly','restart_count')},
            'observation':result,'stable_pod_readback':True,
            'current_image_matches_qualified':before['image_id']=='sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'})
    good=bool(selected) and all(row['observation'].get('status')=='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE' for row in out['sampled_views'])
    out.update(status='PUBLISHED_SKILL_VISIBLE_IN_SAMPLED_ORDINARY_SANDBOXES' if good else 'VISIBILITY_PENDING',
               sampled_nodes=len(selected),all_sampled_views_match=good)
    if not selected:out['error_code']='NO_READY_PUBLIC_MOUNT_CANDIDATE'
    return out

Inspector.run=inspect_views
'''


def build():
    spec = importlib.util.spec_from_file_location('visibility_parent', HERE.parent / 'programmatic-visibility-20261114-r1/build.py')
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    raw, _ = parent.build()
    source = raw.decode()
    installer = (INSTALL / 'install-energy-html-reader-skill-20261114-r1.py').read_bytes()
    assert sha(installer) == '16355e886e6023bfd61bcadda8a8ab44bbc492533441a1f035e0cd21209c373e'
    node = next(n for n in ast.parse(installer.decode()).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'VISIBILITY_CHILD' for t in n.targets))
    child = ast.literal_eval(node.value)
    rows = inventory()
    marker = " need(len(rows)==EXPECTED_SKILL_COUNT and tree==sys.argv[1],'ORDINARY_SKILL_TREE_MISMATCH')"
    assert child.count(marker) == 1
    extra = """ out.update(observed_files=len(rows),observed_tree_sha256=tree,expected_tree_sha256=sys.argv[1])
 expected={row['path']:row for row in EXPECTED_ROWS};actual={row['path']:row for row in rows}
 changes=[]
 for name in sorted(set(expected)|set(actual)):
  if expected.get(name)!=actual.get(name):
   changes.append({'path':name[:180],'expected':expected.get(name),'actual':actual.get(name)})
 out.update(difference_count=len(changes),differences=changes[:16],differences_truncated=len(changes)>16)
"""
    child = 'EXPECTED_ROWS=' + repr(rows) + '\n' + child.replace(marker, extra + marker)
    tree = ast.parse(source)
    assignment = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'VISIBILITY_CHILD' for t in n.targets))
    lines = source.splitlines(keepends=True)
    lines[assignment.lineno - 1:assignment.end_lineno] = ['VISIBILITY_CHILD = ' + repr(child) + '\n']
    source = ''.join(lines)
    source = source.replace('/var/lib/portal-energy-programmatic-skill-install-20261114-r1/REPORT.json', '/var/lib/portal-energy-html-reader-skill-install-20261114-r1/REPORT.json')
    source = source.replace('3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42', RECEIPT_SHA)
    source = source.replace('a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d', TREE)
    source = source.replace('af7750a214e54d2ca4e951667e514e02', OPERATION)
    source = source.replace('fixed_read(RECEIPT, RECEIPT_SHA, 9093)', 'fixed_read(RECEIPT, RECEIPT_SHA, 9671)')
    source = source.replace('energy-programmatic-skill-visibility.v1', 'energy-html-reader-skill-visibility.v1')
    assert source.count("get('run') == 27") == 1
    source = source.replace("get('run') == 27", "get('run') == 34")
    assert source.count('\ndef main():') == 1
    source = source.replace('\ndef main():', '\n' + MULTI_VIEW + '\ndef main():')
    ast.parse(source, feature_version=(3, 7))
    ast.parse(child, feature_version=(3, 7))
    compile(source, '<html-visibility>', 'exec')
    compile(child, '<html-visibility-child>', 'exec')
    return source.encode(), child


def main():
    raw, child = build()
    filename = 'inspect-energy-reader-skill-visibility-20261114-r1.py'
    meta = {'schema': 'energy-html-reader-skill-visibility-package.v1', 'filename': filename, 'bytes': len(raw), 'sha256': sha(raw),
            'accepted_operation': OPERATION, 'accepted_receipt_sha256': RECEIPT_SHA, 'expected_tree_sha256': TREE,
            'visibility_child_sha256': sha(child.encode()), 'max_sampled_nodes': 3, 'read_only': True,
            'production_executed': False, 'messages_sent': 0, 'provider_requests': 0}
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, data in [(filename, raw), ('SOURCE.json', (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode())]:
        path = RELEASE / name
        if path.exists():
            assert path.read_bytes() == data, 'EXISTING_RELEASE_DIFFERS'
        else:
            path.write_bytes(data)
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':
    main()
