"""Read-only verification after the interrupted Gateway 413 -> 414 rollout and accepted independent Skill hotfix.

This does not activate, resume, patch, create, delete, or roll back anything.
It reads the sealed original r4 tool and its existing private journal, returns
immediately while the rollout is incomplete, and otherwise checks all 70 Pods.
Python 3.7+ operator entry. Existing release receipts remain untouched.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

RELEASE_PATH = Path('/home/cnooc/activate-energy-daily-result-links-20261111-r4.py')
RELEASE_SHA = 'e2f8e06d1ecb87849d40a124bd382e429c28bea74562a99aa60d0bdc06d7b7a0'
CONTROL = Path('/var/lib/portal-energy-result-link-20261111-r4')
MAX_SECONDS = 1800
HOTFIX_PATH = Path('/var/lib/portal-energy-browser-fix-install-20261112-r1/REPORT.json')
OLD_TREE = 'f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044'
CODE_PACKAGE_SHA = '1eed96708841b2c7fb2a3b840353a61d799a3b681856fbacff67b7597e5d5138'
RUNTIME_IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
SKILL_READER_R2 = '"""Read only the small 2.0.1 Skill tree; never open browser dependency files."""\nimport hashlib\nimport json\nimport os\nfrom pathlib import Path, PurePosixPath\nimport re\nimport signal\nimport stat\nimport sys\n\nCOUNT = 107\nMAX_TOTAL = 32 * 1024 * 1024\nRUNTIME_DIRECTORY = \'../../.energy-browser-runtime-20261112-r1/linux-arm64\'\n\n\ndef need(ok, code):\n    if not ok:\n        raise ValueError(code)\n\n\ndef sha(raw):\n    return hashlib.sha256(raw).hexdigest()\n\n\ndef canonical(value):\n    return json.dumps(value, sort_keys=True, separators=(\',\', \':\'), ensure_ascii=True, allow_nan=False).encode(\'ascii\')\n\n\ndef no_links(path):\n    for parent in (path,) + tuple(path.parents):\n        state = parent.lstat()\n        need(not stat.S_ISLNK(state.st_mode), \'SKILL_LINK_REFUSED\')\n\n\ndef stamp(state):\n    value = state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns\n    return value if os.name == \'nt\' else value + (state.st_ctime_ns,)\n\n\ndef read(path):\n    no_links(path)\n    fd = os.open(str(path), os.O_RDONLY | getattr(os, \'O_NOFOLLOW\', 0) | getattr(os, \'O_NONBLOCK\', 0))\n    try:\n        before = os.fstat(fd)\n        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= 1048576, \'SKILL_FILE_BOUND\')\n        with os.fdopen(fd, \'rb\', closefd=False) as incoming:\n            raw = incoming.read(1048577)\n        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), \'SKILL_FILE_CHANGED\')\n        return raw\n    finally:\n        os.close(fd)\n\n\ndef inventory(root):\n    no_links(root)\n    need(root.is_dir(), \'SKILL_DIRECTORY_REQUIRED\')\n    identity = root.stat().st_dev, root.stat().st_ino\n    rows, total, folders_seen = [], 0, 0\n    def failed(_):\n        raise ValueError(\'SKILL_DIRECTORY_UNREADABLE\')\n    for directory, folders, names in os.walk(root, followlinks=False, onerror=failed):\n        folders_seen += len(folders)\n        need(folders_seen <= 128, \'SKILL_DIRECTORY_BOUND\')\n        for folder in folders:\n            path = Path(directory) / folder\n            no_links(path)\n            need(path.is_dir() and folder != \'.private\', \'SKILL_DIRECTORY_INVALID\')\n        for name in names:\n            path = Path(directory) / name\n            relative = path.relative_to(root).as_posix()\n            need(len(relative) <= 512 and not set(PurePosixPath(relative).parts) & {\'.private\', \'..\'} and \'\\\\\' not in relative, \'SKILL_FILE_PATH\')\n            raw = read(path)\n            total += len(raw)\n            rows.append({\'path\': relative, \'bytes\':len(raw), \'sha256\':sha(raw)})\n            need(len(rows) <= COUNT and total <= MAX_TOTAL, \'SKILL_TREE_BOUND\')\n    need((root.stat().st_dev, root.stat().st_ino) == identity, \'SKILL_DIRECTORY_CHANGED\')\n    return sorted(rows, key=lambda row: row[\'path\'])\n\n\ndef inspect(root, expected_tree, runtime_manifest):\n    need(root.is_absolute() and \'..\' not in root.parts and re.fullmatch(\'[0-9a-f]{64}\', expected_tree)\n         and re.fullmatch(\'[0-9a-f]{64}\', runtime_manifest), \'SKILL_READER_ARGUMENTS\')\n    first, second = inventory(root), inventory(root)\n    need(first == second and len(second) == COUNT and sha(canonical(second)) == expected_tree, \'SKILL_TREE_CHANGED\')\n    files = {row[\'path\']:{\'bytes\':row[\'bytes\'],\'sha256\':row[\'sha256\']} for row in second if row[\'path\'] != \'MANIFEST.json\'}\n    manifest_raw = read(root / \'MANIFEST.json\')\n    manifest = json.loads(manifest_raw)\n    need(manifest.get(\'schema\') == \'energy-daily-portable-files.v1\' and manifest.get(\'version\') == \'2.0.1-portable\'\n         and manifest.get(\'files\') == files, \'SKILL_MANIFEST_CHANGED\')\n    binding = json.loads(read(root / \'assets/browser-runtime.json\'))\n    need(binding == {\'schema\':\'energy-browser-runtime-binding.v1\',\'directory\':RUNTIME_DIRECTORY,\'manifest_sha256\':runtime_manifest}, \'BROWSER_BINDING_CHANGED\')\n    # The runtime dependency is deliberately outside root. Its existence,\n    # binary hashes and real PNG were accepted by the installer qualification.\n    # This Gateway reader reconciles only the small binding file.\n    need(inventory(root) == second, \'SKILL_TREE_CHANGED\')\n    return {\'status\':\'ENERGY_BROWSER_FIX_SKILL_TREE_VERIFIED\',\'tree_sha256\':expected_tree,\'files\':COUNT,\n            \'skill_manifest_sha256\':sha(manifest_raw),\'runtime_manifest_sha256\':runtime_manifest,\n            \'read_only\':True,\'runtime_dependency_files_read\':0,\'browser_launched\':False,\'credentials_read\':False}\n\n\ndef main():\n    out = {\'status\':\'INCOMPLETE\',\'read_only\':True,\'runtime_dependency_files_read\':0,\'browser_launched\':False,\'credentials_read\':False}\n    try:\n        need(len(sys.argv) == 4, \'SKILL_READER_ARGUMENTS\')\n        if os.name == \'posix\':\n            def expired(*_):\n                raise ValueError(\'SKILL_READER_TIME_BOUND\')\n            signal.signal(signal.SIGALRM, expired)\n            signal.alarm(25)\n        def audit(event, args):\n            if event.startswith(\'socket.\') or event in (\'subprocess.Popen\',\'os.system\',\'sqlite3.connect\'):\n                raise ValueError(\'SKILL_READER_EXTERNAL_OPERATION\')\n            if event == \'open\' and len(args) > 2 and isinstance(args[2], int):\n                need(not args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), \'SKILL_READER_WRITE_REFUSED\')\n        sys.addaudithook(audit)\n        out.update(inspect(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))\n    except Exception as exc:\n        out.update(error_class=type(exc).__name__,error_code=str(exc) if re.fullmatch(\'[A-Z0-9_]{1,100}\',str(exc)) else \'SKILL_READER_FAILED_REDACTED\')\n    finally:\n        if os.name == \'posix\':\n            signal.alarm(0)\n    print(json.dumps(out,sort_keys=True))\n    return int(out[\'status\'] == \'INCOMPLETE\')\n\n\nif __name__ == \'__main__\':\n    sys.exit(main())\n'


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse_hotfix_receipt(receipt):
    need(isinstance(receipt, dict) and receipt.get('schema') == 'energy-browser-fix-install.v1'
         and receipt.get('status') == 'ENERGY_BROWSER_FIX_INSTALLED_RUNTIME_ACCEPTED'
         and receipt.get('skill_updated') is True and receipt.get('maintenance_pod_absent') is True
         and receipt.get('code_package_sha256') == CODE_PACKAGE_SHA, 'ACCEPTED_BROWSER_FIX_INSTALL_REQUIRED')
    publication, runtime, qualification = (receipt.get(name) for name in ('publication','runtime','shared_runtime_qualification'))
    need(all(isinstance(value, dict) for value in (publication,runtime,qualification)), 'BROWSER_FIX_RECEIPT_SHAPE')
    tree, manifest = publication.get('after_tree_sha256'), runtime.get('manifest_sha256')
    need(publication.get('before_tree_sha256') == OLD_TREE and type(publication.get('after_files')) is int
         and publication['after_files'] == 107 and isinstance(tree,str) and re.fullmatch('[0-9a-f]{64}',tree)
         and tree != OLD_TREE and isinstance(manifest,str) and re.fullmatch('[0-9a-f]{64}',manifest)
         and runtime.get('source_image_id') == RUNTIME_IMAGE_ID, 'BROWSER_FIX_PACKAGE_OR_RUNTIME_CHANGED')
    need(qualification.get('status') == 'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED'
         and qualification.get('uid') == 1000 and qualification.get('gid') == 1000
         and qualification.get('package_sha256') == CODE_PACKAGE_SHA
         and qualification.get('runtime_manifest_sha256') == manifest and qualification.get('after_tree_sha256') == tree
         and isinstance(qualification.get('png_sha256'),str) and re.fullmatch('[0-9a-f]{64}',qualification['png_sha256']),
         'BROWSER_FIX_NONROOT_QUALIFICATION_CHANGED')
    return {'after_tree_sha256':tree,'after_files':107,'runtime_manifest_sha256':manifest,'code_package_sha256':CODE_PACKAGE_SHA,
            'runtime_source_image_id':RUNTIME_IMAGE_ID,'qualified_uid':1000,'qualified_gid':1000,'qualified_png_sha256':qualification['png_sha256']}


def load_hotfix_binding():
    # Absence permits the unchanged original f880 Skill. Any existing malformed
    # receipt, insecure directory, unreadable file, or link is a refusal instead
    # of a fallback to historical acceptance.
    path = HOTFIX_PATH
    for parent in tuple(path.parents):
        try:
            state = parent.lstat()
        except FileNotFoundError:
            continue
        need(stat.S_ISDIR(state.st_mode) and not stat.S_ISLNK(state.st_mode), 'BROWSER_FIX_RECEIPT_PARENT_LINK')
        if os.name == 'posix':
            need(state.st_uid == 0 and not stat.S_IMODE(state.st_mode) & 0o022, 'BROWSER_FIX_RECEIPT_PARENT_OWNERSHIP')
            if parent == path.parent:
                need(not stat.S_IMODE(state.st_mode) & 0o077, 'BROWSER_FIX_RECEIPT_DIRECTORY_NOT_PRIVATE')
    try:
        state = path.lstat()
    except FileNotFoundError:
        return None
    need(stat.S_ISREG(state.st_mode) and state.st_nlink == 1 and not stat.S_ISLNK(state.st_mode), 'BROWSER_FIX_RECEIPT_TYPE')
    if os.name == 'posix':
        need(state.st_uid == 0 and not stat.S_IMODE(state.st_mode) & 0o077, 'BROWSER_FIX_RECEIPT_NOT_ROOT_PRIVATE')
    raw = read(path)
    binding = parse_hotfix_receipt(json.loads(raw))
    binding.update(path=str(path),receipt_sha256=sha(raw))
    return binding


def read(path, maximum=4194304):
    path = Path(path)
    need(path.is_absolute(), 'ABSOLUTE_READ_PATH_REQUIRED')
    for member in (path,) + tuple(path.parents):
        state = member.lstat()
        need(not stat.S_ISLNK(state.st_mode) and not getattr(state, 'st_file_attributes', 0) & 0x400, 'READ_PATH_LINK')
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum, 'READ_FILE_BOUND')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(descriptor)
        # Windows reports different creation/ctime values through handle and
        # path APIs. Linux production retains the ctime mutation guard.
        stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                               value.st_ctime_ns if os.name == 'posix' else None)
        need(len(raw) == before.st_size and stamp(before) == stamp(after) == stamp(path.stat()), 'READ_FILE_CHANGED')
        return raw
    finally:
        os.close(descriptor)


def load_release(path=RELEASE_PATH):
    raw = read(path)
    need(sha(raw) == RELEASE_SHA, 'ORIGINAL_R4_SCRIPT_CHANGED')
    module = types.ModuleType('sealed_r4_for_readonly_postcheck')
    module.__file__ = str(path)
    exec(compile(raw, '<sealed-original-r4>', 'exec'), module.__dict__)
    assets = module.load_assets()
    return module, assets


def health_source(release):
    return ('import json,sys,http.client,signal\ng={\'__name__\':\'source_reader\'}\nexec('
            + repr(release.load_assets()['pre'].CHILD) + ',g,g)\n' + r'''
def stop(*args):raise ValueError('FLEET_CHECK_TIMEOUT')
signal.signal(signal.SIGALRM,stop);signal.alarm(20)
out={'status':'INCOMPLETE'}
try:
 out=g['inspect'](g['ROOT'],json.loads(sys.argv[1]))
 c=http.client.HTTPConnection('127.0.0.1',8001,timeout=5)
 try:
  c.request('GET','/health',headers={'Connection':'close'})
  r=c.getresponse(); body=r.read(65537)
  if r.status!=200 or len(body)>65536:raise ValueError('LOCAL_HEALTH_FAILED')
  out['local_health_status']=r.status
 finally:c.close()
except Exception:
 out={'status':'INCOMPLETE','error_code':'FIXED_SOURCE_OR_HEALTH_FAILED'}
finally:signal.alarm(0)
print(json.dumps(out,sort_keys=True))
sys.exit(int(out['status']=='INCOMPLETE'))
''')


def journal_snapshot(release, control=CONTROL):
    need(control == release.CONTROL, 'ORIGINAL_CONTROL_DIRECTORY_CHANGED')
    names = sorted(control.glob('JOURNAL-*.json'))
    need(1 <= len(names) <= 128, 'JOURNAL_COUNT_BOUND')
    need([path.name for path in names] == ['JOURNAL-%04d.json' % index for index in range(len(names))], 'JOURNAL_SEQUENCE_CHANGED')
    raw = read(names[-1])
    receipt = json.loads(raw)
    operation = receipt.get('operation')
    need(isinstance(operation, str) and re.fullmatch('[0-9a-f]{32}', operation), 'JOURNAL_OPERATION')
    need(receipt.get('schema') == 'energy-daily-gateway-result-link-activation.v4'
         and receipt.get('once_claimed') is True and receipt.get('patch_acknowledged') is True
         and receipt.get('patch_request_count') == 1 and receipt.get('maintenance_absent') is True,
         'ACKNOWLEDGED_PATCH_JOURNAL_REQUIRED')
    before_raw = read(control / 'BEFORE-DEPLOYMENT.json')
    expected_raw = read(control / 'CANDIDATE-SPEC.json')
    before, expected = json.loads(before_raw), json.loads(expected_raw)
    release.load_assets()['pre'].deployment_guard(before)
    need(receipt.get('before') == release.strict_identity(before), 'JOURNAL_BASELINE_CHANGED')
    cm = receipt.get('configmap', {})
    need(cm.get('name') == 'gateway-energy-links-' + operation[:12]
         and isinstance(cm.get('uid'), str) and re.fullmatch('[0-9a-f-]{36}', cm['uid']), 'SOURCE_CONFIGMAP_BINDING')
    need(expected == release.candidate_spec(before, cm['name']), 'SAVED_CANDIDATE_SCOPE_CHANGED')
    sample = receipt.get('sample', {})
    need(isinstance(sample.get('image_id_sha256'), str) and re.fullmatch('[0-9a-f]{64}', sample['image_id_sha256']), 'BASELINE_IMAGE_ID_BINDING')
    qualification = receipt.get('qualification', {})
    need(qualification.get('status') == 'GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED'
         and qualification.get('candidate_files_verified') == 6, 'ORIGINAL_QUALIFICATION_REQUIRED')
    binding = {'operation': operation, 'journal': names[-1].name, 'journal_sha256': sha(raw),
               'before_sha256': sha(before_raw), 'candidate_spec_sha256': sha(expected_raw), 'original_r4_script_sha256': RELEASE_SHA}
    return receipt, before, expected, binding


def readonly_updater_type(release):
    class ReadonlyUpdater(release.Updater):
        def __init__(self, assets):
            super().__init__(assets)
            self.allowed_health_source = health_source(release)
            self.hotfix_binding = None
            self.report = {'schema':'energy-daily-gateway-result-link-postcheck.v2', 'status':'INCOMPLETE',
                           'workload_modified':False, 'persistent_files_modified':False, 'original_receipts_modified':False,
                           'application_started':False, 'explicit_database_queries':0, 'secret_api_reads':0,
                           'credentials_exported':False, 'provider_requests':0, 'work_messages_sent':0,
                           'once_claimed':False, 'business_acceptance_pending':True,
                           'service_matches_original_pre_rollout_snapshot_proven':False}

        def journal(self, phase):
            self.report['last_step'] = phase

        def command(self, args, input_bytes=None, **kwargs):
            need(input_bytes is None and isinstance(args, list) and args and args[0] in ('get', 'exec'), 'READONLY_COMMAND_REQUIRED')
            need(time.monotonic() - self.started < MAX_SECONDS and self.commands < 600, 'POSTCHECK_TIME_OR_COMMAND_BOUND')
            if args[0] == 'exec':
                need(len(args) >= 11 and args[1] in self.known_pods and args[2:5] == ['-c', 'gateway', '--']
                     and args[5:10] == ['python3', '-I', '-B', '-u', '-c']
                     and args[10] in (self.allowed_health_source, self.assets['skill_reader'].decode(), SKILL_READER_R2), 'FIXED_READONLY_CHILD_REQUIRED')
            return super().command(args, input_bytes=None, **kwargs)

        def verify_installed_skill(self, pod):
            if self.hotfix_binding is None:
                return super().verify_installed_skill(pod)
            containers = pod['spec'].get('containers', [])
            need(len(containers) == 1 and containers[0].get('name') == 'gateway', 'ONE_GATEWAY_CONTAINER_REQUIRED')
            volumes = [row for row in pod['spec'].get('volumes', [])
                       if row.get('persistentVolumeClaim', {}).get('claimName') == 'deer-flow-skills-rwx-pvc']
            need(len(volumes) == 1, 'SKILLS_PVC_BINDING_CHANGED')
            volume = volumes[0]
            mounts = [row for row in containers[0].get('volumeMounts', [])
                      if row.get('name') == volume['name'] and not row.get('subPath') and not row.get('subPathExpr')]
            need(bool(mounts), 'SHARED_SKILLS_FULL_MOUNT_REQUIRED')
            for row in mounts:
                name = row.get('mountPath')
                need(isinstance(name,str) and name.startswith('/') and '\\' not in name
                     and len(release.PurePosixPath(name).parts) > 1 and '..' not in release.PurePosixPath(name).parts, 'SKILLS_MOUNT_PATH_INVALID')
            mount = sorted(mounts,key=lambda row:row['mountPath'])[0]
            root = str(release.PurePosixPath(mount['mountPath']) / 'public' / 'energy-market-daily')
            binding = self.hotfix_binding
            result = self.child(pod['metadata']['name'],'gateway',SKILL_READER_R2,
                                arguments=(root,binding['after_tree_sha256'],binding['runtime_manifest_sha256']),timeout=40)
            need(result.get('status') == 'ENERGY_BROWSER_FIX_SKILL_TREE_VERIFIED' and result.get('files') == 107
                 and result.get('tree_sha256') == binding['after_tree_sha256'] and result.get('read_only') is True
                 and result.get('runtime_manifest_sha256') == binding['runtime_manifest_sha256']
                 and result.get('runtime_dependency_files_read') == 0 and result.get('browser_launched') is False
                 and result.get('credentials_read') is False, 'BROWSER_FIX_SKILL_VIEW_CHANGED')
            return {'pod':pod['metadata']['name'],'uid':pod['metadata']['uid'],'files':107,'tree_sha256':binding['after_tree_sha256'],
                    'mount_path':mount['mountPath'],'verification_is_read_only':True,
                    'runtime_manifest_sha256':binding['runtime_manifest_sha256'],'runtime_dependency_files_read':0,
                    'mount_declared_read_only':mount.get('readOnly') is True or volume['persistentVolumeClaim'].get('readOnly') is True}

        def run_postcheck(self):
            receipt, before, expected, binding = journal_snapshot(release)
            self.hotfix_binding = load_hotfix_binding()
            if self.hotfix_binding is not None:
                self.report['accepted_browser_fix_install'] = self.hotfix_binding
                self.report['live_runtime_dependency_files_read'] = 0
                self.report['runtime_dependency_verification_basis'] = 'ROOT_OWNED_INSTALL_AND_UID1000_PNG_QUALIFICATION'
            self.before, self.expected = before, expected
            self.operation = receipt['operation']
            self.cm_name = receipt['configmap']['name']
            self.report['accepted_original_rollout'] = binding
            deployment = self.get('deployment', 'gateway')
            release.candidate_guard(deployment, expected)
            status = deployment.get('status', {})
            self.report['current'] = dict(release.strict_identity(deployment), desired=deployment['spec'].get('replicas'),
                                          observed_generation=status.get('observedGeneration', 0), total=status.get('replicas', 0),
                                          updated=status.get('updatedReplicas', 0), ready=status.get('readyReplicas', 0),
                                          available=status.get('availableReplicas', 0), terminating=status.get('terminatingReplicas', 0))
            converged = (status.get('observedGeneration') == 414
                         and all(status.get(key, 0) == 70 for key in ('replicas','updatedReplicas','readyReplicas','availableReplicas'))
                         and status.get('terminatingReplicas', 0) == 0)
            if not converged:
                self.report.update(status='GATEWAY_414_ROLLOUT_IN_PROGRESS_NO_CHANGE', all_gateway_pods_verified=False,
                                   repeat_readonly_check_safe=True, rollout_waited=False)
                return self.report
            cm = self.get('configmap', self.cm_name)
            need(cm['metadata']['uid'] == receipt['configmap']['uid'] and cm.get('immutable') is True
                 and cm['metadata'].get('labels', {}).get(release.LABEL) == self.operation
                 and cm.get('data') == release.configmap_manifest(self.operation, self.cm_name, self.assets)['data'], 'SOURCE_CONFIGMAP_CHANGED')
            need(release.openssl_identity(deployment, self.get('configmap', release.OPENSSL_CM)) == receipt['openssl_compatibility'], 'OPENSSL_COMPAT_CHANGED')
            candidates = self.pre.parse_candidates(self.command(self.pre.LIST_ARGS))
            need(len(candidates) == 70, 'FINAL_READY_COUNT')
            self.known_pods.update(name for name, uid in candidates)
            self.sample = self.get('pod', candidates[0][0])
            need(self.sample['metadata']['uid'] == candidates[0][1]
                 and sha(self.sample['status']['containerStatuses'][0]['imageID'].encode()) == receipt['sample']['image_id_sha256'], 'BASELINE_IMAGE_ID_CHANGED')
            self.service = self.get('service', 'gateway')
            need(self.service['spec'].get('selector') == {'app':'gateway'}, 'GATEWAY_SERVICE_SELECTOR_CHANGED')
            self.verify(deployment)
            # Re-read immutable private evidence and guard the completed check
            # against an operator replacing the original release journal.
            need(journal_snapshot(release)[3] == binding, 'ORIGINAL_EVIDENCE_CHANGED_DURING_POSTCHECK')
            need(load_hotfix_binding() == self.hotfix_binding, 'BROWSER_FIX_EVIDENCE_CHANGED_DURING_POSTCHECK')
            pods = self.report.pop('verified_pods')
            self.report.update(verified_pod_count=len(pods), verified_pods_sha256=sha(canonical(pods)),
                               status='GATEWAY_414_RESULT_LINKS_READONLY_RUNTIME_ACCEPTED', rollout_waited=False)
            return self.report

    return ReadonlyUpdater


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-readonly', required=True, action='store_true')
    parser.parse_args()
    updater = None
    out = {'schema':'energy-daily-gateway-result-link-postcheck.v2', 'status':'INCOMPLETE', 'workload_modified':False,
           'persistent_files_modified':False, 'original_receipts_modified':False, 'secret_api_reads':0,
           'credentials_exported':False, 'provider_requests':0, 'work_messages_sent':0}
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        release, assets = load_release()
        updater = readonly_updater_type(release)(assets)
        out = updater.report
        updater.run_postcheck()
    except Exception as exc:
        out['status'] = 'INCOMPLETE'
        out['error_class'] = type(exc).__name__
        out['error_code'] = str(exc) if re.fullmatch('[A-Z0-9_]{1,100}', str(exc)) else 'POSTCHECK_FAILED_REDACTED'
    out.update(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), queries=updater.commands if updater else 0,
               elapsed_seconds=round(time.monotonic() - updater.started, 3) if updater else 0)
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
