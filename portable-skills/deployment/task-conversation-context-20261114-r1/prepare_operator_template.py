"""Derive an UNSEALED, fail-closed operator template from the accepted r5 tool."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent / "scheduler-budget-20261113-r5/activate-scheduler-budget-20261113-r5.py"


def replace_function(source, name, replacement, owner=None):
    tree = ast.parse(source)
    nodes = tree.body if owner is None else next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == owner).body
    node = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = source.splitlines(True)
    return "".join(lines[:node.lineno - 1]) + replacement.rstrip() + "\n" + "".join(lines[node.end_lineno:])


def main():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "fac6ff86da851e06b6f59ec88fcffefeedeb61064d6c82650ee5b72e09ace04a"
    source = raw.decode()
    start = source.index('"""', 3) + 3
    source = '"""Gateway 416 -> 417: add only the current-conversation Bash context overlay.\n\nUNSEALED TEMPLATE: refuses production execution until independent review/sealing.\nOne new immutable ConfigMap and one volume/mount; existing config and secrets\nremain unchanged. --verify-readonly never writes. --verify-and-save-receipt\nonly verifies the cluster and saves a local immutable acceptance receipt.\n"""' + source[start:]
    replacements = {
        "BEFORE_GENERATION = 415": "BEFORE_GENERATION = 416",
        "BEFORE_SPEC_SHA = '162716342c8bbc067e5d6441dd00e0ab235139f626a1df691037a56bd1c6fb52'": "BEFORE_SPEC_SHA = '3924fcb409ba6982b1d0acb909a276529c86ccd9d814f85af22a838479113df6'",
        "OLD_CM = 'deer-flow-budget-10000-bf39d16f673d2259'": "OLD_CM = 'deer-flow-scheduler-budget-10000-b160057a087d8483'",
        "OLD_CONFIG_SHA = 'bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7'\nOLD_CONFIG_BYTES = 15990": "BUDGET_PLAN_PATH = Path('/var/lib/portal-scheduler-budget-20261113-r5/PLAN.json')\nBUDGET_PLAN_SHA = 'd5432e73ccb749580ce698e4d98b0e812f041f9bfdfc295eba76da0373caeaad'\nBUDGET_OPERATION = 'b904a96416294639ae3acb1245728084'\nEXTRA_CM = 'deer-flow-extensions-config'\nTOOLS_PATH = '/app/backend/packages/harness/deerflow/sandbox/tools.py'\nTOOLS_PARENT = {'bytes': 99410, 'sha256': 'a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c'}\nTOOLS_CANDIDATE = {'bytes': 100988, 'sha256': 'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330'}\nTOOLS_VOLUME = 'task-conversation-context'\nCODE_ASSET = {}  # Sealer fills only after independent source review.",
        "CONTROL = Path('/var/lib/portal-scheduler-budget-20261113-r5')": "CONTROL = Path('/var/lib/portal-task-conversation-context-20261114-r1')",
        "LABEL = 'portal.scheduler-budget/operation'": "LABEL = 'portal.task-conversation-context/operation'",
        "'scheduler-budget-activation.v1'": "'task-conversation-context-activation.v1'",
        "'scheduler-budget-private-plan.v1'": "'task-conversation-context-private-plan.v1'",
        "'tool_revision': 'r5'": "'tool_revision': 'r1'",
        "all_existing_gateway_spec_fields_except_configmap_name_preserved": "all_existing_gateway_spec_fields_preserved",
        "set(OVERLAYS) | {OLD_CM}": "set(OVERLAYS) | {OLD_CM, EXTRA_CM}",
        "'CREATE_NEW_BUDGET_CONFIGMAP'": "'CREATE_NEW_CONTEXT_CONFIGMAP'",
        "[OLD_CM] + sorted(OVERLAYS)": "[OLD_CM, EXTRA_CM] + sorted(OVERLAYS)",
        "need(len(result) == 13, 'SOURCE_BINDING_COUNT')": "need(len(result) == 14, 'SOURCE_BINDING_COUNT')",
        "'WAIT_EXISTING_STRATEGY_GATEWAY_416_ROLLOUT'": "'WAIT_EXISTING_STRATEGY_GATEWAY_417_ROLLOUT'",
        "'WAIT_GATEWAY_416_ROLLOUT'": "'WAIT_GATEWAY_417_ROLLOUT'",
        "'READONLY_VERIFY_GATEWAY_416'": "'READONLY_VERIFY_GATEWAY_417'",
        "'SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED'": "'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED'",
        "'SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED'": "'TASK_CONTEXT_PATCH_NOT_OBSERVED_REVIEW_REQUIRED'",
        "'GATEWAY_416_ROLLOUT_IN_PROGRESS_NO_CHANGE'": "'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE'",
        "'RUNTIME_ACCEPTED_FUTURE_SCHEDULED_RUN_ACCEPTANCE_PENDING'": "'CONTEXT_RUNTIME_ACCEPTED_BUSINESS_ACCEPTANCE_PENDING'",
        "verified_source_files_per_pod=13": "verified_source_files_per_pod=14",
    }
    for old, new in replacements.items():
        assert old in source, old
        source = source.replace(old, new)
    source = source.replace("{'phase': 'WAIT_GATEWAY_417_ROLLOUT', 'desired': 70,", "{'phase': 'WAIT_GATEWAY_417_ROLLOUT', 'generation': current['metadata']['generation'], 'desired': 70,")
    source = source.replace("    def __init__(self, child, readonly=False):\n", "    def __init__(self, child, readonly=False):\n        self.code = ''\n")
    anchor = "\n\nclass Refused(ValueError):"
    source = source.replace(anchor, "\nCORE_SOURCES[TOOLS_PATH] = (TOOLS_PARENT['bytes'], TOOLS_PARENT['sha256'])\nEXPECTED_SOURCES[TOOLS_PATH] = dict(TOOLS_PARENT)\n" + anchor)
    source = replace_function(source, "replace_budget", '''def accepted_budget_profile():
    raw = private_read(BUDGET_PLAN_PATH)
    need(sha(raw) == BUDGET_PLAN_SHA, 'ACCEPTED_BUDGET_PLAN_HASH')
    plan = json.loads(raw)
    need(plan.get('schema') == 'scheduler-budget-private-plan.v1'
         and plan.get('operation') == BUDGET_OPERATION
         and plan.get('candidate_spec_sha256') == BEFORE_SPEC_SHA
         and plan.get('new_configmap') == OLD_CM
         and plan.get('new_config_bytes') == 15992
         and re.fullmatch('[0-9a-f]{64}', plan.get('new_config_sha256', ''))
         and OLD_CM.endswith(plan['new_config_sha256'][:16]), 'ACCEPTED_BUDGET_PLAN_BINDING')
    return {'bytes': plan['new_config_bytes'], 'sha256': plan['new_config_sha256'], 'scheduler_recursion_limit': 10000}


def load_code():
    need(set(CODE_ASSET) == {'base64', 'sha256'}, 'CODE_ASSET_NOT_SEALED')
    need(isinstance(CODE_ASSET['base64'], str) and len(CODE_ASSET['base64']) <= 262144, 'CODE_ASSET_BOUND')
    raw = base64.b64decode(CODE_ASSET['base64'], validate=True)
    need({'bytes': len(raw), 'sha256': sha(raw)} == TOOLS_CANDIDATE
         and CODE_ASSET['sha256'] == TOOLS_CANDIDATE['sha256'], 'CODE_ASSET_HASH')
    # datax-01 may run Python 3.7; tools.py belongs to Gateway's newer Python.
    # Compilation happens only in the existing Gateway qualification child.
    return raw.decode('utf-8')
''')
    source = replace_function(source, "build_spec", '''def build_spec(deployment, cm_name):
    need(identity(deployment) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION,
                                  'spec_sha256': BEFORE_SPEC_SHA}, 'GATEWAY_BASELINE_CHANGED')
    need(deployment['spec'].get('replicas') == 70 and not deployment['spec'].get('paused', False), 'GATEWAY_REPLICAS_OR_PAUSE')
    need(re.fullmatch('gateway-task-context-[0-9a-f]{12}', cm_name), 'NEW_CONFIGMAP_NAME')
    config_volume(deployment['spec'], OLD_CM)
    pod = deployment['spec']['template']['spec']
    need(pod['containers'][0].get('image') == IMAGE, 'FRESH_BASELINE_IMAGE_CHANGED')
    volumes, mounts = pod.get('volumes', []), pod['containers'][0].get('volumeMounts', [])
    need(all(row.get('name') != TOOLS_VOLUME for row in volumes + mounts), 'CONTEXT_VOLUME_ALREADY_PRESENT')
    target = PurePosixPath(TOOLS_PATH)
    for mount in mounts:
        base = PurePosixPath(mount['mountPath'])
        need(base.is_absolute() and '..' not in base.parts, 'MOUNT_SHAPE')
        need(base != target and base not in target.parents and target not in base.parents, 'TOOLS_SHADOW_MOUNT')
    spec = copy.deepcopy(deployment['spec'])
    changed = spec['template']['spec']
    changed['volumes'].append({'name': TOOLS_VOLUME, 'configMap': {'name': cm_name}})
    changed['containers'][0]['volumeMounts'].append({'name': TOOLS_VOLUME, 'mountPath': TOOLS_PATH, 'subPath': 'tools.py', 'readOnly': True})
    restored = copy.deepcopy(spec)
    restored['template']['spec']['volumes'].pop()
    restored['template']['spec']['containers'][0]['volumeMounts'].pop()
    need(restored == deployment['spec'], 'DEPLOYMENT_PATCH_SCOPE_EXCEEDED')
    return spec
''')
    source = replace_function(source, "cas_patch", '''def cas_patch(deployment, spec):
    meta = deployment['metadata']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
            {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'add', 'path': '/spec/template/spec/volumes/-', 'value': spec['template']['spec']['volumes'][-1]},
            {'op': 'add', 'path': '/spec/template/spec/containers/0/volumeMounts/-', 'value': spec['template']['spec']['containers'][0]['volumeMounts'][-1]}]
''')
    source = replace_function(source, "new_configmap", '''def new_configmap(name, operation, candidate):
    need(re.fullmatch('gateway-task-context-[0-9a-f]{12}', name) and re.fullmatch('[0-9a-f]{32}', operation), 'NEW_CONFIGMAP_IDENTITY')
    need({'bytes': len(candidate), 'sha256': sha(candidate)} == TOOLS_CANDIDATE, 'CANDIDATE_SOURCE_HASH')
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': NS,
            'labels': {LABEL: operation}}, 'immutable': True, 'data': {'tools.py': candidate.decode('utf-8')}}
''')
    source = replace_function(source, "qualification", '''    def qualification(self, pod, candidate=False):
        sources = copy.deepcopy(self.plan['sources'])
        if not candidate:
            sources[TOOLS_PATH] = dict(TOOLS_CANDIDATE)
        payload = {'mode': 'qualify' if candidate else 'verify', 'sources': sources, 'config': self.plan['config']}
        if candidate:
            raw = self.manifest['data']['tools.py'].encode('utf-8')
            payload['candidate'] = dict(TOOLS_CANDIDATE, base64=base64.b64encode(raw).decode('ascii'))
        result = self.command(['exec', '-i', pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child],
                              body=canonical(payload), timeout=40, limit=65536)
        if result.get('status') != 'TASK_CONTEXT_READONLY_QUALIFIED':
            self.report['qualification_error_class'] = result.get('error_class') if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', str(result.get('error_class', ''))) else 'UNKNOWN'
            self.report['qualification_error_code'] = result.get('error_code') if re.fullmatch('[A-Z][A-Z0-9_]{0,95}', str(result.get('error_code', ''))) else 'CHILD_QUALIFICATION_FAILED'
        need(result.get('status') == 'TASK_CONTEXT_READONLY_QUALIFIED' and result.get('stable_readback') is True
             and result.get('context_injection_synthetic_checks_accepted') is True, 'READONLY_CONTEXT_QUALIFICATION_FAILED')
        return result
''', "Operator")
    source = source.replace("self.journal('READ_GATEWAY_415_AND_FIXED_CONFIGMAPS')", "self.journal('READ_GATEWAY_416_AND_PRESERVED_CONFIGMAPS')")
    source = source.replace("        candidate = replace_budget(raw)\n        name = 'deer-flow-scheduler-budget-10000-' + sha(candidate)[:16]\n", "        profile = accepted_budget_profile()\n        need({'bytes': len(raw), 'sha256': sha(raw)} == {key: profile[key] for key in ('bytes', 'sha256')}, 'BUDGET_CONFIG_CHANGED')\n        candidate = self.code.encode('utf-8')\n        name = 'gateway-task-context-' + self.operation[:12]\n")
    source = source.replace("new_configmap(original, name, self.operation, candidate)", "new_configmap(name, self.operation, candidate)")
    source = source.replace("            'new_config_bytes': len(candidate), 'new_config_sha256': sha(candidate), 'sources': sources,", "            'config': profile, 'accepted_budget_plan_sha256': BUDGET_PLAN_SHA, 'sources': sources,\n            'candidate_source': dict(TOOLS_CANDIDATE),")
    source = source.replace("for key in ('config_sha256', 'candidate_sha256', 'scheduler_recursion_limit', 'candidate_scheduler_recursion_limit',\n                        'max_recursion_limit', 'source_files_verified', 'versions', 'semantic_change_only_scheduler_recursion_limit')", "for key in ('config_sha256', 'scheduler_recursion_limit', 'max_recursion_limit', 'source_files_verified',\n                        'versions', 'context_injection_synthetic_checks_accepted', 'isolated_source_functions_not_loaded_application_modules')")
    source = source.replace("candidate_config_sha256=sha(candidate)", "candidate_source_sha256=sha(candidate)")
    old = "        rebuilt = replace_budget(maps[OLD_CM]['data']['config.yaml'].encode('utf-8'))\n        need(new_configmap(maps[OLD_CM], self.plan['new_configmap'], self.operation, rebuilt) == self.manifest\n"
    new = "        need(self.plan['config'] == accepted_budget_profile() and self.plan['accepted_budget_plan_sha256'] == BUDGET_PLAN_SHA, 'PRIVATE_BUDGET_BINDING')\n        rebuilt = self.code.encode('utf-8')\n        need(new_configmap(self.plan['new_configmap'], self.operation, rebuilt) == self.manifest\n"
    assert old in source
    source = source.replace(old, new)
    source = source.replace("and len(patch) == 5 and patch[2]", "and len(patch) == 6 and patch[2]")
    source = source.replace("and patch[4] == cas_patch(self.before, self.expected)[4], 'PRIVATE_PATCH_SCOPE')", "and patch[4:] == cas_patch(self.before, self.expected)[4:], 'PRIVATE_PATCH_SCOPE')")
    source = source.replace("            business_acceptance_pending=True)\n        self.journal", "            business_acceptance_pending=True, context_injection_runtime_accepted=True,\n            context_injection_qualification_basis='ISOLATED_SOURCE_FUNCTIONS_AND_TRANSPORT_PREFIX_WITH_SYNTHETIC_TASK_ID',\n            accepted_source={'parent_sha256': TOOLS_PARENT['sha256'], 'candidate_sha256': TOOLS_CANDIDATE['sha256']})\n        self.journal")
    source = replace_function(source, "main", '''def save_acceptance(operator):
    need(operator.report.get('status') == 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED', 'ACCEPTANCE_NOT_COMPLETE')
    target = CONTROL / 'RUNTIME-ACCEPTANCE.json'
    if target.exists():
        raw = private_read(target)
        prior = json.loads(raw)
        need(prior.get('schema') == operator.report['schema'] and prior.get('status') == operator.report['status']
             and prior.get('operation') == operator.operation and prior.get('plan_sha256') == operator.report['plan_sha256']
             and prior.get('after') == operator.report['after'] and prior.get('accepted_source') == operator.report['accepted_source']
             and prior.get('all_gateway_pods_verified') is True and prior.get('context_injection_runtime_accepted') is True,
             'EXISTING_ACCEPTANCE_MISMATCH')
    else:
        operator.report['persistent_files_modified'] = True
        raw = canonical(operator.report)
        private_write(target, raw)
    operator.report['runtime_acceptance_receipt'] = {'path': str(target), 'bytes': len(raw), 'sha256': sha(raw)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--activate-task-context-once', action='store_true')
    group.add_argument('--verify-readonly', action='store_true')
    group.add_argument('--verify-and-save-receipt', action='store_true')
    options = parser.parse_args(argv)
    readonly = options.verify_readonly or options.verify_and_save_receipt
    operator = Operator('', readonly=readonly)
    operator.report.update(local_acceptance_write_requested=not options.verify_readonly,
                           cluster_verification_readonly=readonly)
    try:
        need(sys.version_info >= (3, 7), 'PYTHON_37_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_REQUIRED')
        need(os.geteuid() == 0, 'ROOT_REQUIRED')
        os.umask(0o077)
        operator.child = load_child()
        operator.code = load_code()
        operator.run()
    except KeyboardInterrupt:
        operator.report.update(error_class='KeyboardInterrupt', error_code='OPERATOR_INTERRUPTED_ROLLOUT_MAY_CONTINUE',
                               verify_readonly_available=operator.report['patch_may_have_applied'])
    except Exception as exc:
        operator.report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE')
        if isinstance(exc, Refused):
            if isinstance(getattr(exc, 'field', None), str) and re.fullmatch('[A-Za-z][A-Za-z0-9]{0,63}', exc.field):
                operator.report['error_field'] = exc.field
            if isinstance(getattr(exc, 'path', None), str) and re.fullmatch(r'/[A-Za-z0-9_~./-]{1,1023}', exc.path):
                operator.report['error_path'] = exc.path
    finally:
        operator.report.update(commands=operator.commands, elapsed_seconds=round(time.monotonic() - operator.started, 3),
                               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        if not options.verify_readonly and operator.report['status'] == 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED':
            try:
                save_acceptance(operator)
            except Exception as exc:
                operator.report.update(status='RUNTIME_ACCEPTED_RECEIPT_SAVE_INCOMPLETE', receipt_save_error_class=type(exc).__name__)
        if operator.report['once_claimed'] and not operator.readonly:
            try:
                operator.journal('FINAL_OPERATOR_RESULT')
                report_raw = canonical(operator.report)
                private_write(CONTROL / 'REPORT.json', report_raw)
                operator.report['report_path'] = str(CONTROL / 'REPORT.json')
                operator.report['report_bytes'] = len(report_raw)
                operator.report['report_sha256'] = sha(report_raw)
            except Exception as exc:
                operator.report['report_write_error_class'] = type(exc).__name__
        print(json.dumps(operator.report, sort_keys=True), flush=True)
    return 0 if operator.report['status'] in ('TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE') else 1
''')
    compile(source, '<unsealed-task-context-template>', 'exec')
    assert source.count('CODE_ASSET = {}') == source.count('CHILD_ASSET = {}') == 1
    assert 'replace_budget(' not in source and 'OLD_CONFIG_SHA' not in source and 'OLD_CONFIG_BYTES' not in source
    target = ROOT / 'activate-task-conversation-context-20261114-r1.py'
    target.write_text(source, encoding='utf-8', newline='\n')
    (ROOT / 'OPERATOR-PARENT-SOURCE.json').write_text(json.dumps({'schema': 'task-context-operator-parent.v1', 'path': str(PARENT.relative_to(ROOT.parents[2])).replace('\\', '/'), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'historical_source_modified': False, 'template_sealed': False, 'production_requests': 0}, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'UNSEALED_TEMPLATE_PREPARED', 'bytes': target.stat().st_size}))


if __name__ == '__main__':
    main()
