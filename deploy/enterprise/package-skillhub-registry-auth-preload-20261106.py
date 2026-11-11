"""Generate the fixed datax-01 SkillHub cache preloader; no production access.

The accepted full-OCI importer and its three sealed helpers are retained. This
generator changes only component pins, the explicit larger archive budget,
SkillHub old-reference policy, and fresh control-plane node/resource checks.
The generated tool does not start SkillHub or authorize a deployment.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tarfile
import time
import types

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / 'release/sidechat-skillhub-security-20261105-r1/preload-as-delivered/preload-portal-frontend-sidechat-security-datax02-20261105-r1.py'
TEMPLATE_SHA = 'bc94910416d06d6a6bfc4e409f3b36d6f54e3e36d49ebea71e1bda68feaff682'
NAME = 'preload-portal-skillhub-registry-auth-datax01-20261106-r1.py'
TRANSPORT = {'bytes': 848886784, 'sha256': 'd2e8fbebeddc1a4667dfae26eefb5a9f83e79ce4fcb1724361e09d1d4c561728'}
MANIFEST = 'sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68'
CONFIG = 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7'
REPO = 'deerflow.local/skillhub-server'
TAG = REPO + ':20261106-registry-auth-r1'
OLD_ROOT = 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2'
OLD_TAG = REPO + ':20260908-enterprise-arm64-rc15-skillhubsession2'
OLD_RUNTIME = 'docker.io/library/import-2026-10-09@' + OLD_ROOT
OLD_PLATFORM = 'sha256:b4a18e1955e51ba8fb7ec65df91ee081378ba4f84a63177a850f25e82fd9971a'
NODE_UID = 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def assign(source, name, value):
    rows = source.splitlines(keepends=True)
    matches = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    require(len(matches) == 1, 'UNIQUE_ASSIGNMENT_' + name)
    node = matches[0]
    rows[node.lineno - 1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
    return ''.join(rows)


def change(source, old, new):
    require(source.count(old) == 1, 'UNIQUE_SOURCE_REPLACEMENT_' + old[:50])
    return source.replace(old, new)


def load(source):
    module = types.ModuleType('_skillhub_preload_candidate')
    exec(compile(source, NAME, 'exec'), module.__dict__)
    module.HOST = 'datax-01'
    return module


def build(archive, output):
    raw = TEMPLATE.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == TEMPLATE_SHA, 'QUALIFIED_TEMPLATE_CHANGED')
    require(archive.stat().st_size == TRANSPORT['bytes'], 'ARCHIVE_SIZE_CHANGED')
    digest = hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 ** 2), b''):
            digest.update(block)
    require(digest.hexdigest() == TRANSPORT['sha256'], 'ARCHIVE_HASH_CHANGED')
    with tarfile.open(archive, 'r:') as tar:
        manifest_raw = tar.extractfile('blobs/sha256/' + MANIFEST[7:]).read()
        config_raw = tar.extractfile('blobs/sha256/' + CONFIG[7:]).read()
    require('sha256:' + hashlib.sha256(manifest_raw).hexdigest() == MANIFEST
            and 'sha256:' + hashlib.sha256(config_raw).hexdigest() == CONFIG, 'IMAGE_METADATA_CHANGED')
    manifest, config = json.loads(manifest_raw), json.loads(config_raw)
    chain = ''
    for diff in config['rootfs']['diff_ids']:
        chain = diff if not chain else 'sha256:' + hashlib.sha256((chain + ' ' + diff).encode()).hexdigest()
    require(len(manifest['layers']) == len(config['rootfs']['diff_ids']) == 15, 'LAYER_COUNT_CHANGED')
    source = raw.decode().replace('\r\n', '\n')
    source = source.replace('portal-frontend-ui-cache-preload.v1', 'portal-skillhub-registry-auth-cache-preload.v1')
    source = source.replace('COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED', 'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED')
    source = source.replace('fixed_frontend_node_required', 'fixed_skillhub_node_required')
    source = source.replace('original_frontend_cache_identity_changed', 'original_skillhub_cache_identity_changed')
    source = source.replace('audited_datax02_imagefs_changed', 'audited_datax01_imagefs_changed')
    source = source.replace('DATAX02_ROOT_IMAGEFS', 'DATAX01_ROOT_IMAGEFS')
    source = source.replace('--preload-frontend-sidechat-security-once', '--preload-skillhub-registry-auth-once')
    replacements = dict(TRANSPORT_PIN=TRANSPORT,
        INPUT='/home/cnooc/skillhub.registry-auth-r1.full.oci.tar',
        CONTROL='/var/lib/portal-skillhub-registry-auth-preload-20261106-r1',
        NODES={'datax-01': NODE_UID}, REPO=REPO, TAG=TAG, MANIFEST=MANIFEST,
        CONFIG=CONFIG, FINAL_CHAIN=chain, DESCRIPTORS=manifest['layers'],
        PROTECTED={OLD_TAG: OLD_ROOT})
    for key, value in replacements.items():
        source = assign(source, key, value)
    source = change(source, 'MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = (2387, 8906, 11)',
                    'MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = ' + repr((len(manifest_raw), len(config_raw), 15)))
    source = change(source, 'ARCHIVE_MAX, RAW_MAX, HEADERS_MAX = 128*MIB, 2*GIB, 100000',
                    'ARCHIVE_MAX, RAW_MAX, HEADERS_MAX = GIB, 2*GIB, 100000')
    source = change(source, 'PROTECTED = ' + repr(replacements['PROTECTED']),
                    'PROTECTED = ' + repr(replacements['PROTECTED']) + '\nOPTIONAL_PROTECTED = ' +
                    repr({OLD_RUNTIME: OLD_ROOT, REPO+'@'+OLD_ROOT: OLD_ROOT, REPO+'@'+OLD_PLATFORM: OLD_PLATFORM}))
    source = change(source, "or name in PROTECTED, 'reference_scope')",
                    "or name in PROTECTED or name in OPTIONAL_PROTECTED, 'reference_scope')")
    source = change(source, "require(actual == expected if expected is not None else actual in (None, MANIFEST), 'existing_reference_changed')",
                    "require(actual in (None, MANIFEST) if name in (TAG, REPO+'@'+MANIFEST) and expected is None else actual == expected, 'existing_reference_changed')")
    source = change(source, "            self.report['references_before'] = dict(self.refs)",
                    "            for name, expected in OPTIONAL_PROTECTED.items():\n"
                    "                actual = self.target(name)\n"
                    "                require(actual in (None, expected), 'optional_old_reference_identity_changed')\n"
                    "                self.refs[name] = actual\n"
                    "            self.report['references_before'] = dict(self.refs)")
    # The old image ID is an index. Presence of its optional image-record alias
    # is not inferred from runtime imageID; absent aliases remain absent.
    source = change(source, "            tools.append(None)".strip(),
                    "tools.append(shutil.which('kubectl', path=SAFE_PATH))\n        require(tools[2], 'kubectl_required_for_fresh_node_check')")
    source = change(source, "        preclaim = early_preload_gate(I, R, F, tools, original, started)",
                    "        fresh = fresh_controlplane(R, tools[2], original.run_bounded, started)\n"
                    "        preclaim = early_preload_gate(I, R, F, tools, original, started)\n"
                    "        preclaim['controlplane'] = fresh\n"
                    "        preclaim['old_and_candidate_references'] = preclaim_references(I, tools[0], original.run_bounded, started)")
    source = change(source, "        def fresh_budget(self):\n            return worker_budget(self)",
                    "        def fresh_budget(self):\n"
                    "            node = self.R.node_identity(self.query('fresh_node_identity', ['get', 'node', HOST, '-o=jsonpath='+self.R.NODE_PATH]))\n"
                    "            policy = self.R.thresholds(self.I.parse(self.query('fresh_kubelet_thresholds', ['get', '--raw', '/api/v1/nodes/'+HOST+'/proxy/configz'])))\n"
                    "            self.report['kubernetes_node_uid_freshly_checked'] = True\n"
                    "            self.report['fresh_node'] = node\n"
                    "            self.report['effective_kubelet_policy'] = policy\n"
                    "            return worker_budget(self)")
    source = change(source, "expected_node_uid_basis='HISTORICAL_UID_AND_EXACT_HOST_PLATFORM;FRESH_API_CHECK_REQUIRED_AT_CUTOVER'",
                    "expected_node_uid_basis='OPERATOR_PIN_AND_FRESH_API_BEFORE_EACH_IMPORT_PHASE'")
    source = change(source, "                deployment_patch=False, pod_deletion=False, running_cutover_process_modified=False)",
                    "                deployment_patch=False, pod_deletion=False, running_cutover_process_modified=False,\n"
                    "                application_started=False, provider_requests=0, tool_revision='r1')")
    source = change(source, "expected_node_uid=NODES.get(HOST), exitcode=0 if good else 1, automatic_retry=False, production_workload_mutation=False, ready_for_rollout=False)",
                    "expected_node_uid=NODES.get(HOST), exitcode=0 if good else 1, automatic_retry=False, production_workload_mutation=False, ready_for_rollout=False,\n"
                    "                   database_access=False, application_started=False, provider_requests=0, tool_revision='r1')")
    source = change(source, "        def query(self, phase, args):\n",
                    "        def query(self, phase, args):\n            require(len(self.report['commands']) < 140, 'command_limit')\n")
    source = change(source, "require(len(self.report['commands']) < 200, 'command_limit')",
                    "require(len(self.report['commands']) < 140, 'command_limit')")
    source = change(source,
                    "                require(args[:3] in (['images', 'import', '--local'], ['images', 'tag', '--local'])\n"
                    "                        and '--force' not in args and '--digests' not in args, 'mutation_scope')",
                    "                base = ['images', 'import', '--local', '--platform', 'linux/arm64', '--snapshotter', 'overlayfs', '--label', 'io.cri-containerd.image=managed']\n"
                    "                path = '/proc/self/fd/' + str(self.archive_fd)\n"
                    "                imports = [base+[path], base+['--no-unpack', path]]\n"
                    "                tags = [['images', 'tag', '--local', self.temp, n] for n in (TAG, REPO+'@'+MANIFEST)]\n"
                    "                require(not is_cri and ((args in imports and pass_fds == (self.archive_fd,)) or (args in tags and not pass_fds)), 'mutation_scope')")
    source = change(source, "    source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n    target = None",
                    "    require(os.path.realpath(INPUT) == INPUT, 'input_parent_symlink')\n"
                    "    source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)\n    target = None")
    source = change(source, "    capacity = worker_budget(probe)\n    source = os.open(INPUT",
                    "    capacity = worker_budget(probe)\n    require(os.path.realpath(INPUT) == INPUT, 'input_parent_symlink')\n    source = os.open(INPUT")
    fresh = '''def fresh_controlplane(R, kubectl, runner, started):
    results = []
    queries = [(['get', 'node', HOST, '-o=jsonpath='+R.NODE_PATH], R.node_identity),
               (['get', '--raw', '/api/v1/nodes/'+HOST+'/proxy/configz'], lambda raw: R.thresholds(json.loads(raw)))]
    for args, validate in queries:
        time_gate(started)
        argv = [kubectl, '--kubeconfig', '/etc/kubernetes/admin.conf', '--request-timeout=20s'] + args
        raw, unused, receipt = runner(argv, timeout=min(20, TOTAL_SECONDS-(time.monotonic()-started)), limit=MIB)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'fresh_controlplane_read_failed')
        results.append(validate(raw))
    return {'node': results[0], 'kubelet_policy': results[1], 'queries': 2, 'mutations': 0}


def preclaim_references(I, ctr, runner, started):
    refs = {}
    expected = dict(PROTECTED)
    expected.update(OPTIONAL_PROTECTED)
    expected.update({TAG: MANIFEST, REPO+'@'+MANIFEST: MANIFEST})
    for name, wanted in sorted(expected.items()):
        time_gate(started)
        argv = [ctr, '--address', I.SOCKET, '--namespace', 'k8s.io', 'images', 'ls', 'name=='+name]
        raw, unused, receipt = runner(argv, timeout=min(20, TOTAL_SECONDS-(time.monotonic()-started)), limit=MIB)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'preclaim_reference_read_failed')
        actual = I.image_target(raw, name)
        require(actual == wanted if name in PROTECTED else actual in (None, wanted), 'preclaim_image_reference_changed')
        refs[name] = actual
    return refs


'''
    source = change(source, 'def main():\n', fresh + 'def main():\n')
    # Compute early cost from the actual complete archive, not guessed layer
    # sizes. This also independently validates every compressed and raw digest.
    module = load(source)
    I, unused, unused2 = module.load_inputs()
    with archive.open('rb') as stream:
        prepared = module.inspect_archive(stream.fileno(), I, time.monotonic())
    early = {k: prepared[k] for k in ('manifest', 'chains', 'blob_sizes', 'layer_stats')}
    source = assign(source, 'EARLY_BUDGET', early)
    source = '"""Fixed SkillHub datax-01 cache preload; never starts the application.\nGenerated from a sealed field-tested importer; original helper pins retained.\n"""\n' + source
    ast.parse(source, feature_version=(3, 7))
    output.mkdir(parents=True, exist_ok=True)
    path = output / NAME
    path.write_bytes(source.encode())
    result = {'script': NAME, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'template_sha256': TEMPLATE_SHA, 'transport': TRANSPORT, 'manifest': MANIFEST, 'config': CONFIG,
              'layers': 15, 'all_blob_and_diffid_hashes_checked': True, 'application_started': False,
              'production_access': False, 'ready_for_rollout': False}
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    build(args.archive, args.output)
