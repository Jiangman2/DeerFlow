"""Source-only replacements for the retained, field-qualified operator."""


def accepted_profile():
    path = Path('/var/lib/portal-scheduler-concurrency-20261116-r2/PLAN.json')
    raw = private_read(path)
    need(sha(raw) == 'cbc3e668c0f2c5cbe9a31a66ff3ac2d56908ad86adb5f5b15ed8a307b043a48d', 'ACCEPTED_CONCURRENCY_PLAN_HASH')
    plan = json.loads(raw)
    need(plan.get('operation') == 'f0f944d091bc434caff845c24d19a836'
         and plan.get('candidate_spec_sha256') == BEFORE_SPEC_SHA
         and plan.get('new_configmap') == OLD_CM
         and plan.get('new_config_sha256') == 'a4d2f3af8d2f08895a29b977b83512e05fd5d958a75340f88bec39390d26a0fe'
         and type(plan.get('new_config_bytes')) is int and 0 < plan['new_config_bytes'] < 65536,
         'ACCEPTED_CONCURRENCY_PLAN_BINDING')
    return {'bytes': plan['new_config_bytes'], 'sha256': plan['new_config_sha256'],
            'max_concurrent_runs': 32, 'multi_instance': True}


def load_code():
    need(set(CODE_ASSET) == {'base64', 'bytes', 'sha256'}, 'CODE_NOT_SEALED')
    raw = base64.b64decode(CODE_ASSET['base64'], validate=True)
    need(0 < len(raw) < 65536 and {'bytes': len(raw), 'sha256': sha(raw)} == CODE_META
         and CODE_ASSET['sha256'] == CODE_META['sha256'], 'CODE_ASSET_HASH')
    return raw


def replace_scheduler(raw):
    need(False, 'CONFIGURATION_MUTATION_NOT_SUPPORTED')


def build_spec(deployment, cm_name):
    need(identity(deployment) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION,
                                 'spec_sha256': BEFORE_SPEC_SHA}, 'GATEWAY_BASELINE_CHANGED')
    need(deployment['spec'].get('replicas') == 70 and not deployment['spec'].get('paused', False), 'GATEWAY_REPLICAS_OR_PAUSE')
    need(re.fullmatch('gateway-personal-ready-[0-9a-f]{12}', cm_name), 'NEW_CONFIGMAP_NAME')
    config_volume(deployment['spec'], OLD_CM)
    pod = deployment['spec']['template']['spec']
    need(len(pod['containers']) == 1 and pod['containers'][0].get('image') == IMAGE, 'FRESH_BASELINE_IMAGE_CHANGED')
    volumes, mounts = pod.get('volumes', []), pod['containers'][0].get('volumeMounts', [])
    need(all(row.get('name') != FIX_VOLUME for row in volumes + mounts), 'FIX_VOLUME_ALREADY_PRESENT')
    selected = [i for i, row in enumerate(mounts) if row.get('mountPath') == TARGET]
    need(len(selected) == 1, 'EXISTING_NOTIFICATION_MOUNT_REQUIRED')
    index = selected[0]
    old = mounts[index]
    volume = next((row for row in volumes if row.get('name') == old.get('name')), {})
    need(volume.get('configMap', {}).get('name') == LINK_CM
         and old.get('readOnly') is True and old.get('subPath') == 'work_notification_delivery.py'
         and not old.get('subPathExpr'), 'EXISTING_NOTIFICATION_MOUNT_CHANGED')
    target = PurePosixPath(TARGET)
    need(all(i == index or PurePosixPath(row['mountPath']) != target
             and PurePosixPath(row['mountPath']) not in target.parents
             and target not in PurePosixPath(row['mountPath']).parents for i, row in enumerate(mounts)),
         'NOTIFICATION_SHADOW_MOUNT')
    spec = copy.deepcopy(deployment['spec'])
    changed = spec['template']['spec']
    changed['volumes'].append({'name': FIX_VOLUME, 'configMap': {'name': cm_name, 'defaultMode': 420}})
    changed['containers'][0]['volumeMounts'][index]['name'] = FIX_VOLUME
    restored = copy.deepcopy(spec)
    restored['template']['spec']['volumes'].pop()
    restored['template']['spec']['containers'][0]['volumeMounts'][index] = copy.deepcopy(old)
    need(restored == deployment['spec'], 'DEPLOYMENT_PATCH_SCOPE_EXCEEDED')
    return spec


def new_configmap(name, operation, candidate):
    need(re.fullmatch('gateway-personal-ready-[0-9a-f]{12}', name)
         and re.fullmatch('[0-9a-f]{32}', operation), 'NEW_CONFIGMAP_IDENTITY')
    need({'bytes': len(candidate), 'sha256': sha(candidate)} == CODE_META, 'CANDIDATE_SOURCE_HASH')
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': NS,
            'labels': {LABEL: operation}}, 'immutable': True,
            'data': {'work_notification_delivery.py': candidate.decode('utf-8')}}


def cas_patch(deployment, spec):
    meta = deployment['metadata']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
            {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'replace', 'path': '/spec', 'value': spec}]


def operator_qualification(self, pod, candidate=False):
    sources = copy.deepcopy(self.plan['sources'])
    if not candidate:
        sources[TARGET] = dict(CODE_META)
    payload = {'mode': 'qualify' if candidate else 'verify', 'sources': sources, 'config': self.plan['config']}
    if candidate:
        payload['candidate'] = CODE_ASSET
    result = self.command(['exec', '-i', pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child],
                          body=canonical(payload), timeout=60, limit=65536)
    if result.get('status') != 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED':
        for key in ('error_class', 'error_code'):
            value = result.get(key, 'UNKNOWN')
            self.report['qualification_' + key] = value if re.fullmatch('[A-Za-z0-9_]{1,95}', str(value)) else 'UNKNOWN'
    need(result.get('status') == 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED'
         and result.get('stable_readback') is True and result.get('isolated_notification_cases', 0) >= 8,
         'READONLY_NOTIFICATION_QUALIFICATION_FAILED')
    return result


def operator_preflight(self):
    self.journal('READ_CURRENT_GATEWAY_AND_PRESERVED_CONFIGMAPS')
    before = self.get('deployment', 'gateway')
    need(identity(before) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION, 'spec_sha256': BEFORE_SPEC_SHA}, 'GATEWAY_BASELINE_CHANGED')
    guard_deployment(before, before['spec'], BEFORE_GENERATION, True)
    profile = accepted_profile()
    maps = self.snapshot_maps()
    raw = maps[OLD_CM].get('data', {}).get('config.yaml', '').encode('utf-8')
    need({'bytes': len(raw), 'sha256': sha(raw)} == {key: profile[key] for key in ('bytes', 'sha256')}, 'CURRENT_CONFIG_CHANGED')
    candidate = load_code()
    name = 'gateway-personal-ready-' + self.operation[:12]
    self.before, self.expected = before, build_spec(before, name)
    self.manifest = new_configmap(name, self.operation, candidate)
    sources = source_binding(before['spec'], {key: maps[key] for key in OVERLAYS})
    service = self.get('service', 'gateway')
    need(service['metadata'].get('namespace') == NS and not service['metadata'].get('deletionTimestamp')
         and service['spec'].get('selector') == before['spec']['selector'].get('matchLabels'), 'GATEWAY_SERVICE_SELECTOR')
    self.plan = {'schema': 'energy-personal-notification-private-plan.v1', 'operation': self.operation,
        'before': identity(before), 'after_spec_sha256': sha(canonical(self.expected)), 'new_configmap': name,
        'config': profile, 'candidate_source': dict(CODE_META), 'sources': sources,
        'old_configmaps': {key: cm_identity(value) for key, value in maps.items()},
        'service': {'uid': service['metadata']['uid'], 'spec_sha256': sha(canonical(service['spec']))},
        'before_file_sha256': sha(canonical(before)), 'candidate_spec_sha256': sha(canonical(self.expected)),
        'candidate_configmap_sha256': sha(canonical(self.manifest)), 'child_sha256': sha(self.child.encode('utf-8'))}
    rows = parse_pod_list(self.command(POD_LIST_ARGS))
    need(len(rows) == 70, 'BASELINE_ACTIVE_POD_COUNT')
    self.known_pods.update(row['metadata']['name'] for row in rows)
    selected = rows[0]
    sample = self.get('pod', selected['metadata']['name'])
    need(sample['metadata']['uid'] == selected['metadata']['uid'], 'PREFLIGHT_LISTED_POD_REPLACED')
    rs_name = owner(sample, 'ReplicaSet')['name']
    self.known_rs.add(rs_name)
    rs = self.get('replicaset', rs_name)
    stamp = pod_guard(sample, rs, before)
    self.plan['image_id'] = sample['status']['containerStatuses'][0]['imageID']
    qualification = self.qualification(sample['metadata']['name'], True)
    need(pod_guard(self.get('pod', sample['metadata']['name']), rs, before) == stamp, 'PREFLIGHT_SAMPLE_CHANGED')
    self.check_maps()
    fresh = self.get('deployment', 'gateway')
    guard_deployment(fresh, before['spec'], BEFORE_GENERATION, True)
    self.before = fresh
    self.plan['before_file_sha256'] = sha(canonical(fresh))
    self.report.update(before=identity(fresh), sample=stamp,
        qualification={key: qualification[key] for key in ('config_sha256', 'max_concurrent_runs',
            'scheduler_multi_instance', 'scheduler_recursion_limit', 'max_recursion_limit', 'source_files_verified',
            'versions', 'isolated_notification_cases', 'postgres_query_compiled')},
        candidate_source_sha256=sha(candidate), candidate_spec_sha256=sha(canonical(self.expected)),
        skill_modified=False, group_webhook_modified=False, resources_modified=False,
        old_gateway419_all_pod_acceptance_claimed=False)
    return maps
