"""Read-only postcheck template: no create, patch, delete, retry or rollback.

UNSEALED and unbound until the actual operator-supplied spec difference proves
the sole allowed API default. --verify-and-save-receipt writes only a new local
acceptance directory after full runtime verification; old release files remain
unchanged. No old activation main function is executed.
"""
import argparse
import base64
import copy
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

PARENT_ASSET = {}  # Filled only after source review and field-diff proof.
FIELD_DEFAULTING = {}  # Must bind one real, exact observed defaulting difference.
PARENT_SHA = 'dff040f55c50089d3f1b3f3c738f00699ed2ec844fc476182c81ef63dd8309c8'
OLD_CONTROL = Path('/var/lib/portal-task-conversation-context-20261114-r1')
NEW_CONTROL = Path('/var/lib/portal-task-conversation-context-postcheck-20261114-r2')
OPERATION = '86bbcfd1fb7f40819284fa431ecce9f1'
PLAN_SHA = '880c16308caa9e09267a9cee041577caa0088cbf2b90100cbb5e5a91649bea0f'
REPORT_BYTES = 2698
REPORT_SHA = '5f14fb064db5c1d8ea563080f54f5955f3690d08e63665aec25959128999f464'
REQUESTED_SPEC_SHA = 'd9a8a00422329427b737b3f67d1c13f35482a16d6614cbc3365b705ad198b616'


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_parent():
    need(set(PARENT_ASSET) == {'base64', 'sha256'} and PARENT_ASSET['sha256'] == PARENT_SHA, 'PARENT_ASSET_NOT_SEALED')
    encoded = PARENT_ASSET['base64']
    need(isinstance(encoded, str) and len(encoded) <= 524288, 'PARENT_ASSET_BOUND')
    raw = base64.b64decode(encoded, validate=True)
    need(0 < len(raw) <= 393216 and digest(raw) == PARENT_SHA, 'PARENT_ASSET_HASH')
    module = types.ModuleType('immutable_parent_operator_not_main')
    exec(compile(raw, '<immutable-r1-operator>', 'exec'), module.__dict__)
    need(module.CONTROL == OLD_CONTROL, 'PARENT_CONTROL_SCOPE')
    return module


def defaulted_expected(parent, requested, proof):
    """Accept precisely one already-observed API default; never hide other drift."""
    need(set(proof) == {'schema', 'field_report_sha256', 'gateway_uid', 'generation', 'requested_spec_sha256',
                       'actual_spec_sha256', 'difference_path', 'actual_default_mode', 'difference_count'}, 'FIELD_PROOF_KEYS')
    need(proof['schema'] == 'task-context-single-defaulting-field-proof.v1'
         and proof['gateway_uid'] == parent.DEPLOYMENT_UID and proof['generation'] == 417
         and proof['requested_spec_sha256'] == REQUESTED_SPEC_SHA
         and proof['difference_count'] == 1 and type(proof['actual_default_mode']) is int
         and proof['actual_default_mode'] == 420
         and re.fullmatch('[0-9a-f]{64}', proof['field_report_sha256'])
         and re.fullmatch('[0-9a-f]{64}', proof['actual_spec_sha256']), 'FIELD_PROOF_NOT_BOUND')
    need(digest(canonical(requested)) == REQUESTED_SPEC_SHA, 'ORIGINAL_REQUESTED_SPEC_HASH')
    expected = copy.deepcopy(requested)
    volumes = expected['template']['spec']['volumes']
    rows = [(i, volume) for i, volume in enumerate(volumes) if volume.get('name') == parent.TOOLS_VOLUME]
    need(len(rows) == 1, 'ONE_CONTEXT_VOLUME_REQUIRED')
    index, volume = rows[0]
    need(index == len(volumes) - 1 and set(volume) == {'name', 'configMap'}
         and set(volume['configMap']) == {'name'}
         and volume['configMap']['name'] == 'gateway-task-context-' + OPERATION[:12], 'ORIGINAL_CONTEXT_VOLUME_SHAPE')
    path = '/template/spec/volumes/%d/configMap/defaultMode' % index
    need(proof['difference_path'] == path, 'UNRELATED_DEFAULTING_PATH_REFUSED')
    volume['configMap']['defaultMode'] = 420
    need(digest(canonical(expected)) == proof['actual_spec_sha256'], 'OBSERVED_DEFAULTING_SPEC_HASH')
    return expected


def verifier_class(parent):
    class ReadOnlyVerifier(parent.Operator):
        def __init__(self, child):
            super().__init__(child, readonly=True)

        def command(self, args, *values, **kwargs):
            need(args[:1] in (['get'], ['exec']), 'POSTCHECK_MUTATION_COMMAND_REFUSED')
            return super().command(args, *values, **kwargs)

        def preflight(self):
            raise Refused('POSTCHECK_ACTIVATION_REFUSED')

        def claim(self, *args):
            raise Refused('POSTCHECK_ACTIVATION_REFUSED')

        def cutover(self):
            raise Refused('POSTCHECK_ACTIVATION_REFUSED')

        def rollout(self):
            raise Refused('POSTCHECK_WAIT_REFUSED')

        def run_postcheck(self):
            original_raw = parent.private_read(OLD_CONTROL / 'REPORT.json')
            need(len(original_raw) == REPORT_BYTES and digest(original_raw) == REPORT_SHA, 'ORIGINAL_FAILURE_RECEIPT_HASH')
            old = json.loads(original_raw)
            need(old.get('operation') == OPERATION and old.get('plan_sha256') == PLAN_SHA
                 and old.get('status') == 'INCOMPLETE' and old.get('error_code') == 'GATEWAY_SPEC_DRIFT'
                 and old.get('patch_request_count') == 1 and old.get('patch_may_have_applied') is True,
                 'ORIGINAL_FAILURE_SCOPE')
            need(digest(parent.private_read(OLD_CONTROL / 'PLAN.json')) == PLAN_SHA, 'ORIGINAL_PLAN_HASH')
            self.load_plan()
            need(self.operation == OPERATION and self.plan['candidate_spec_sha256'] == REQUESTED_SPEC_SHA, 'ORIGINAL_PLAN_IDENTITY')
            requested = copy.deepcopy(self.expected)
            self.expected = defaulted_expected(parent, requested, FIELD_DEFAULTING)
            self.report.update(tool_revision='postcheck-r2', schema='task-conversation-context-activation.v1',
                original_receipts_modified=False, old_release_directory_modified=False, only_observed_api_default_accepted=True,
                accepted_original_failure={'operation': OPERATION, 'bytes': REPORT_BYTES, 'sha256': REPORT_SHA,
                    'path': str(OLD_CONTROL / 'REPORT.json'), 'plan_sha256': PLAN_SHA},
                accepted_defaulting=copy.deepcopy(FIELD_DEFAULTING), requested_spec_sha256=REQUESTED_SPEC_SHA,
                observed_spec_sha256=digest(canonical(self.expected)), automatic_retry=False, patch_request_count=0,
                configmap_create_requests=0, original_patch_not_replayed=True, workload_modified=False)
            current = self.get('deployment', 'gateway')
            parent.guard_deployment(current, self.expected, 417)
            self.report['current'] = dict(parent.identity(current), **{key: current.get('status', {}).get(key, 0)
                for key in ('observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')})
            if not parent.is_converged(current, 417):
                self.report['status'] = 'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE'
                return
            self.verify(current)
            need(parent.private_read(OLD_CONTROL / 'REPORT.json') == original_raw
                 and digest(parent.private_read(OLD_CONTROL / 'PLAN.json')) == PLAN_SHA, 'ORIGINAL_RECEIPT_READBACK_CHANGED')
    return ReadOnlyVerifier


def save_new_acceptance(parent, operator):
    need(operator.report.get('status') == 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED'
         and operator.report.get('all_gateway_pods_verified') is True
         and operator.report.get('context_injection_runtime_accepted') is True, 'ACCEPTANCE_NOT_COMPLETE')
    parent.safe(NEW_CONTROL)
    if not NEW_CONTROL.exists():
        os.mkdir(str(NEW_CONTROL), 0o700)
    state = NEW_CONTROL.stat()
    need(stat.S_ISDIR(state.st_mode) and state.st_uid == 0 and stat.S_IMODE(state.st_mode) == 0o700, 'NEW_RECEIPT_DIRECTORY_OWNER_MODE')
    target = NEW_CONTROL / 'RUNTIME-ACCEPTANCE.json'
    if target.exists():
        raw = parent.private_read(target)
        existing = json.loads(raw)
        need(all(existing.get(key) == operator.report.get(key) for key in (
            'schema', 'status', 'operation', 'plan_sha256', 'after', 'accepted_source', 'accepted_defaulting',
            'all_gateway_pods_verified', 'context_injection_runtime_accepted')), 'EXISTING_NEW_ACCEPTANCE_MISMATCH')
        reused = True
    else:
        operator.report['persistent_files_modified'] = True
        raw = canonical(operator.report)
        parent.private_write(target, raw)
        reused = False
    operator.report['runtime_acceptance_receipt'] = {'path': str(target), 'bytes': len(raw), 'sha256': digest(raw),
        'existing_acceptance_reused': reused, 'stdout_without_receipt_metadata_matches_saved': not reused}


def finalize_observation(report, operator, started):
    report.update(commands=operator.commands if operator else 0, elapsed_seconds=round(time.monotonic() - started, 3),
                  observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument('--verify-readonly', action='store_true')
    options.add_argument('--verify-and-save-receipt', action='store_true')
    args = parser.parse_args(argv)
    started = time.monotonic()
    parent = operator = None
    observation_finalized = False
    report = {'schema': 'task-conversation-context-activation.v1', 'tool_revision': 'postcheck-r2', 'status': 'INCOMPLETE',
        'readonly': True, 'workload_modified': False, 'persistent_files_modified': False, 'old_release_directory_modified': False,
        'commands': 0, 'patch_request_count': 0, 'configmap_create_requests': 0, 'provider_requests': 0, 'work_messages_sent': 0,
        'credentials_exported': False, 'secret_api_reads': 0, 'original_receipts_modified': False,
        'local_acceptance_write_requested': bool(args.verify_and_save_receipt)}
    try:
        need(sys.version_info >= (3, 7), 'PYTHON_37_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_REQUIRED')
        need(os.geteuid() == 0, 'ROOT_REQUIRED')
        os.umask(0o077)
        need(bool(FIELD_DEFAULTING), 'FIELD_DEFAULTING_PROOF_NOT_BOUND')
        parent = load_parent()
        operator = verifier_class(parent)(parent.load_child())
        operator.code = parent.load_code()
        operator.report.update(report)
        report = operator.report
        operator.run_postcheck()
        finalize_observation(report, operator, started)
        observation_finalized = True
        if args.verify_and_save_receipt and report['status'] == 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED':
            save_new_acceptance(parent, operator)
    except KeyboardInterrupt:
        observation_finalized = False
        report.update(error_class='KeyboardInterrupt', error_code='READONLY_INSPECTION_INTERRUPTED')
    except Exception as exc:
        observation_finalized = False
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) or parent is not None and isinstance(exc, parent.Refused) else 'LOCAL_FAILURE')
        if report['status'] == 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED':
            report['status'] = 'RUNTIME_ACCEPTED_RECEIPT_SAVE_INCOMPLETE'
    finally:
        if not observation_finalized:
            finalize_observation(report, operator, started)
        print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['status'] in ('TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE') else 1


if __name__ == '__main__':
    sys.exit(main())
