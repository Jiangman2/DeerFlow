"""Read-only verification after the interrupted Gateway 413 -> 414 rollout.

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


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
            self.report = {'schema':'energy-daily-gateway-result-link-postcheck.v1', 'status':'INCOMPLETE',
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
                     and args[10] in (self.allowed_health_source, self.assets['skill_reader'].decode()), 'FIXED_READONLY_CHILD_REQUIRED')
            return super().command(args, input_bytes=None, **kwargs)

        def run_postcheck(self):
            receipt, before, expected, binding = journal_snapshot(release)
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
    out = {'schema':'energy-daily-gateway-result-link-postcheck.v1', 'status':'INCOMPLETE', 'workload_modified':False,
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
