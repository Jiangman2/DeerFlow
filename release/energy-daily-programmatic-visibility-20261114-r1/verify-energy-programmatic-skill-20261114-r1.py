"""Read-only postcheck of the accepted 2.1.3 Skill publication."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time

RECEIPT = Path('/var/lib/portal-energy-programmatic-skill-install-20261114-r1/REPORT.json')
RECEIPT_SHA = '3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42'
TREE = 'a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d'
OPERATION = 'af7750a214e54d2ca4e951667e514e02'
VISIBILITY_CHILD = "\nimport hashlib,json,os,pathlib,stat,sys\nout={'status':'INCOMPLETE','files_modified':False,'browser_launched':False,'credentials_read':False,'provider_requests':0}\ndef need(ok,code):\n if not ok:raise ValueError(code)\ndef read(path,limit):\n for part in (path,*path.parents):need(not part.is_symlink(),'SOURCE_LINK')\n before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit,'SOURCE_BOUND')\n raw=path.read_bytes();after=path.stat()\n need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'SOURCE_CHANGED')\n return raw\ntry:\n if os.geteuid()==0:os.setgroups([]);os.setgid(1000);os.setuid(1000)\n need(os.geteuid()==1000 and os.getegid()==1000,'ORDINARY_UID_REQUIRED')\n EXPECTED_SKILL_COUNT = 108\n root=pathlib.Path('/mnt/skills/public/energy-market-daily');runtime=root.parent/'.energy-browser-runtime-20261112-r1/linux-arm64'\n rows=[]\n for directory,folders,names in os.walk(root,followlinks=False):\n  for name in folders:need(not(pathlib.Path(directory)/name).is_symlink(),'SOURCE_LINK')\n  for name in names:\n   path=pathlib.Path(directory)/name;raw=read(path,1048576)\n   rows.append({'path':path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})\n   need(len(rows)<=128,'SKILL_COUNT_BOUND')\n rows.sort(key=lambda row:row['path'])\n tree=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()\n need(len(rows)==EXPECTED_SKILL_COUNT and tree==sys.argv[1],'ORDINARY_SKILL_TREE_MISMATCH')\n raw=read(runtime/'RUNTIME.json',262144);digest=hashlib.sha256(raw).hexdigest()\n need(digest=='cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598','ORDINARY_RUNTIME_MANIFEST_MISMATCH')\n binding=json.loads(read(root/'assets/browser-runtime.json',32768))\n need(binding=={'schema':'energy-browser-runtime-binding.v1','directory':'../.energy-browser-runtime-20261112-r1/linux-arm64','manifest_sha256':digest},'ORDINARY_BINDING_MISMATCH')\n executable=runtime/'chrome-linux/headless_shell';state=executable.stat()\n need(stat.S_ISREG(state.st_mode) and state.st_size==298683784 and os.access(executable,os.R_OK),'ORDINARY_RUNTIME_NOT_READABLE')\n temporary=pathlib.Path('/tmp');space=os.statvfs(temporary)\n out.update(status='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE',uid=os.geteuid(),gid=os.getegid(),skill_tree_sha256=tree,runtime_manifest_sha256=digest,\n  temporary={'path':'/tmp','readable':os.access(temporary,os.R_OK),'writable':os.access(temporary,os.W_OK),'searchable':os.access(temporary,os.X_OK),\n  'noexec':bool(space.f_flag&getattr(os,'ST_NOEXEC',8)),'available_bytes':space.f_bavail*space.f_frsize},\n  nondefault_tmpdir_configured=bool(os.environ.get('TMPDIR')))\nexcept Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,ValueError) else 'ORDINARY_VISIBILITY_FAILED_REDACTED')\nprint(json.dumps(out,sort_keys=True))\nsys.exit(int(out['status']=='INCOMPLETE'))\n"
CLAIM = 'deer-flow-skills-rwx-pvc'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=25s', '-n', 'deer-flow']


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def fixed_read(path, expected_hash, expected_size=None):
    for part in (path,) + tuple(path.parents):
        need(not part.is_symlink(), 'FIXED_FILE_LINK')
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 1048576, 'FIXED_FILE_BOUND')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(1048577)
        after = os.fstat(descriptor)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(stamp(before) == stamp(after) == stamp(path.stat()), 'FIXED_FILE_CHANGED')
        need(len(raw) == before.st_size and (expected_size is None or len(raw) == expected_size)
             and hashlib.sha256(raw).hexdigest() == expected_hash, 'FIXED_FILE_IDENTITY_MISMATCH')
        return raw
    finally:
        os.close(descriptor)


def ready_candidate(pod):
    metadata, spec, status = pod.get('metadata', {}), pod.get('spec', {}), pod.get('status', {})
    if metadata.get('deletionTimestamp') or status.get('phase') != 'Running':
        return None
    if metadata.get('labels', {}).get('app.kubernetes.io/component') != 'sandbox':
        return None
    containers = [x for x in spec.get('containers', []) if x.get('name') == 'sandbox']
    states = [x for x in status.get('containerStatuses', []) if x.get('name') == 'sandbox']
    if len(containers) != 1 or len(states) != 1 or states[0].get('ready') is not True or not states[0].get('state', {}).get('running'):
        return None
    name, uid = metadata.get('name', ''), metadata.get('uid', '')
    need(re.fullmatch(r'sandbox-[a-z0-9-]+', name) and re.fullmatch(r'[a-f0-9-]{36}', uid), 'POD_IDENTITY_INVALID')
    mounts = [x for x in containers[0].get('volumeMounts', []) if x.get('mountPath') == '/mnt/skills/public']
    volumes = {x['name']: x for x in spec.get('volumes', [])}
    mount = mounts[0] if len(mounts) == 1 else {}
    volume = volumes.get(mount.get('name'), {})
    contract = len(mounts) == 1 and mount.get('subPath') == 'public' and not mount.get('subPathExpr') and volume.get('persistentVolumeClaim', {}).get('claimName') == CLAIM
    return {'pod': name, 'uid': uid, 'node': spec.get('nodeName'), 'mount_contract': contract,
            'mount_readonly': mount.get('readOnly', False), 'container_id': states[0].get('containerID'),
            'image_id': states[0].get('imageID'), 'restart_count': states[0].get('restartCount')}


class Inspector:
    def __init__(self, child):
        self.child, self.selected, self.queries = child, None, 0

    def query(self, args):
        allowed = [['get', 'pods', '-l', 'app.kubernetes.io/component=sandbox', '-o', 'json']]
        if self.selected:
            allowed += [['get', 'pod', self.selected['pod'], '-o', 'json'],
                        ['exec', self.selected['pod'], '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', self.child, TREE]]
        need(args in allowed, 'READONLY_FIXED_COMMAND_REQUIRED')
        self.queries += 1
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(KUBE + args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'},
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 40
                while process.poll() is None:
                    need(output.tell() <= 16777216 and errors.tell() <= 65536, 'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic() < deadline, 'COMMAND_TIMEOUT')
                    time.sleep(.1)
                need(process.returncode in ((0, 1) if args[0] == 'exec' else (0,)), 'READONLY_COMMAND_FAILED')
                need(output.tell() <= 16777216 and errors.tell() <= 65536, 'COMMAND_OUTPUT_BOUND')
                output.seek(0)
                return json.loads(output.read(16777217))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def run(self):
        listing = self.query(['get', 'pods', '-l', 'app.kubernetes.io/component=sandbox', '-o', 'json'])
        need(isinstance(listing.get('items'), list) and len(listing['items']) <= 4096, 'POD_LIST_BOUND')
        candidates = [x for x in (ready_candidate(p) for p in listing['items']) if x]
        eligible = [x for x in candidates if x['mount_contract']]
        out = {'ready_ordinary_pods': len(candidates), 'eligible_public_mount_pods': len(eligible),
               'ineligible_public_mount_pods': len(candidates) - len(eligible)}
        if not eligible:
            return dict(out, status='VISIBILITY_PENDING', error_code='NO_READY_PUBLIC_MOUNT_CANDIDATE')
        self.selected = sorted(eligible, key=lambda x: x['pod'])[0]
        before = ready_candidate(self.query(['get', 'pod', self.selected['pod'], '-o', 'json']))
        need(before == self.selected, 'POD_CHANGED_BEFORE_READ')
        result = self.query(['exec', self.selected['pod'], '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', self.child, TREE])
        after = ready_candidate(self.query(['get', 'pod', self.selected['pod'], '-o', 'json']))
        need(after == before, 'POD_CHANGED_DURING_READ')
        need(isinstance(result, dict), 'CHILD_RESPONSE_INVALID')
        out.update(observation=result, stable_pod_readback=True,
                   sample={k: before[k] for k in ('pod', 'uid', 'node', 'mount_readonly', 'restart_count')},
                   current_image_matches_qualified=before['image_id'] == 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8')
        out['status'] = 'PUBLISHED_SKILL_VISIBLE_IN_ORDINARY_SANDBOX' if result.get('status') == 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE' else 'VISIBILITY_PENDING'
        return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-readonly', action='store_true', required=True)
    parser.parse_args()
    out = {'schema': 'energy-programmatic-skill-visibility.v1', 'status': 'INCOMPLETE', 'read_only': True,
           'persistent_files_modified': False, 'original_receipt_modified': False, 'workload_modified': False,
           'secret_api_reads': 0, 'provider_requests': 0, 'work_messages_sent': 0, 'browser_launched': False,
           'temporary_command_spools_cleaned': True, 'business_acceptance_pending': True}
    inspector = None
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        receipt = json.loads(fixed_read(RECEIPT, RECEIPT_SHA, 9093))
        need(receipt.get('operation') == OPERATION and receipt.get('skill_updated') is True
             and receipt['publication']['after_tree_sha256'] == TREE and receipt['publication']['after_files'] == 108,
             'INSTALLED_RECEIPT_MISMATCH')
        need(receipt.get('schema') == 'energy-programmatic-skill-install.v1'
             and receipt.get('status') == 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'
             and receipt.get('maintenance_pod_absent') is True
             and receipt.get('maintenance_configmap_absent') is True
             and receipt.get('before') == receipt.get('after_deployments')
             and receipt.get('gateway_modified') is False
             and receipt.get('shared_runtime_qualification', {}).get('status') == 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED'
             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('run') == 27
             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('passed') is True,
             'INSTALLED_QUALIFICATION_MISMATCH')
        inspector = Inspector(VISIBILITY_CHILD)
        out.update(accepted_receipt_sha256=RECEIPT_SHA, accepted_operation=OPERATION, expected_skill_tree_sha256=TREE)
        out.update(inspector.run())
        fixed_read(RECEIPT, RECEIPT_SHA, 9093)
    except Exception as exc:
        out.update(status='INCOMPLETE', error_class=type(exc).__name__,
                   error_code=str(exc) if isinstance(exc, Refused) else 'READONLY_INSPECTION_FAILED_REDACTED')
    out.update(queries=inspector.queries if inspector else 0, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    raise SystemExit(main())
