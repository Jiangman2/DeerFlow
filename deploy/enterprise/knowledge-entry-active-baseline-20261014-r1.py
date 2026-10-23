"""Read-only baseline of the Nginx actually selected by production Services.

Python 3.7+. No Kubernetes/configuration writes, reloads, provider requests,
secrets, raw startup commands, or raw nginx configuration output.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import types

TOOL = Path('/var/lib/deerflow-kb-ui-r2-TLq4STGh/entry.py')
TOOL_SHA = 'bf9cfc6283e0f02264635dad3375878aae2fad6cfad977d7efbc78249d8a0b40'
DEPLOYMENT = 'nginx-security-heai-20261012-r1'
POD = 'nginx-security-heai-20261012-r1-6cfdf58fdb-2kwjd'
SERVICES = ('nginx', 'nginx-nodeport')


class AuditFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise AuditFailure(label)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def emit(**value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def load_helper():
    for path in reversed([TOOL.parent] + list(TOOL.parent.parents)):
        s = path.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_tool_parent')
    fd = os.open(str(TOOL), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o077 and s.st_size < 131072, 'unsafe_tool_file')
        raw = stream.read(131072)
    require(hashlib.sha256(raw).hexdigest() == TOOL_SHA, 'tool_hash_changed')
    m = types.ModuleType('kb_readonly_helper')
    m.__file__ = str(TOOL)
    exec(compile(raw, str(TOOL), 'exec'), m.__dict__)
    m.POD = POD
    return m


def active_snapshot(m):
    pod = m.get('pod', POD)
    deployment = m.get('deployment', DEPLOYMENT)
    require(not pod['metadata'].get('deletionTimestamp') and not deployment['metadata'].get('deletionTimestamp'), 'target_deleting')
    owner = [x for x in pod['metadata'].get('ownerReferences', []) if x.get('controller')]
    require(len(owner) == 1 and owner[0]['kind'] == 'ReplicaSet', 'pod_owner_changed')
    rs = m.get('replicaset', owner[0]['name'])
    require(rs['metadata']['uid'] == owner[0]['uid'] and any(
        o.get('controller') and o.get('kind') == 'Deployment' and o.get('uid') == deployment['metadata']['uid']
        for o in rs['metadata'].get('ownerReferences', [])), 'deployment_ownership_changed')
    ds = deployment.get('status', {})
    require(deployment['spec'].get('replicas') == 1 and ds.get('observedGeneration') == deployment['metadata']['generation']
            and all(ds.get(k) == 1 for k in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas')), 'deployment_not_converged')
    pcs = [c for c in pod['spec']['containers'] if c['name'] == 'nginx']
    dcs = [c for c in deployment['spec']['template']['spec']['containers'] if c['name'] == 'nginx']
    ready = [c for c in pod.get('status', {}).get('containerStatuses', []) if c['name'] == 'nginx']
    require(len(pcs) == len(dcs) == len(ready) == 1 and pod['status'].get('phase') == 'Running'
            and ready[0].get('ready') and 'running' in ready[0].get('state', {}), 'container_not_ready')
    require(pcs[0]['image'] == dcs[0]['image'] and bool(ready[0].get('containerID')), 'container_image_or_identity_changed')
    bound = {}
    for name in SERVICES:
        service = m.get('service', name)
        selector = service['spec'].get('selector', {})
        require(bool(selector) and all(pod['metadata'].get('labels', {}).get(k) == v for k, v in selector.items()), 'service_selector_changed_' + name)
        ports = [p for p in service['spec'].get('ports', []) if p.get('port') == 2026 and p.get('protocol', 'TCP') == 'TCP']
        require(len(ports) == 1 and ports[0].get('targetPort') == 2026, 'service_port_changed_' + name)
        slices = json.loads(m.command(m.KUBE + ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + name, '-o', 'json']))
        live = []
        for entry in slices.get('items', []):
            require(entry.get('metadata', {}).get('labels', {}).get('kubernetes.io/service-name') == name, 'slice_label_mismatch')
            for endpoint in entry.get('endpoints', []):
                if endpoint.get('conditions', {}).get('ready') is not True or endpoint.get('conditions', {}).get('terminating') is True:
                    continue
                ref = endpoint.get('targetRef', {})
                require(ref.get('kind') == 'Pod' and ref.get('name') == POD and ref.get('uid') == pod['metadata']['uid']
                        and pod['status']['podIP'] in endpoint.get('addresses', []), 'service_has_other_ready_backend_' + name)
                require(any(p.get('port') == 2026 and p.get('protocol', 'TCP') == 'TCP' for p in entry.get('ports', [])), 'endpoint_port_changed')
                live.append(ref['uid'])
        require(len(live) == 1, 'requires_one_ready_endpoint_' + name)
        bound[name] = {'uid': service['metadata']['uid'], 'spec_sha256': digest(service['spec']),
                       'selector': selector, 'ready_pod': POD, 'ready_pod_uid': live[0]}
    return pod, deployment, pcs[0], ready[0], bound


def main():
    stage = 'HELPER_VALIDATION'
    try:
        require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_on_datax01')
        m = load_helper()
        stage = 'SERVICE_BACKEND_IDENTITY'
        pod, deployment, container, status, services = active_snapshot(m)
        emit(active_entry='VERIFIED_FROM_SERVICES', deployment=DEPLOYMENT, pod=POD,
             deployment_uid=deployment['metadata']['uid'], deployment_spec_sha256=digest(deployment['spec']),
             pod_uid=pod['metadata']['uid'], container_id=status['containerID'], image=container['image'],
             image_id=status.get('imageID'), services=services)
        startup = ' '.join(container.get('command', []) + container.get('args', []))
        mounts = container.get('volumeMounts', [])
        emit(startup_mentions_tmp_config='/tmp/nginx.conf' in startup,
             startup_mentions_template='/etc/nginx/nginx.conf.template' in startup,
             config_mounts=[{k: v.get(k) for k in ('name', 'mountPath', 'subPath', 'readOnly')}
                            for v in mounts if 'nginx.conf' in v.get('mountPath', '')])
        stage = 'CONFIGMAP_FINGERPRINTS'
        for volume in pod['spec'].get('volumes', []):
            if 'configMap' not in volume:
                continue
            name = volume['configMap']['name']
            cm = m.get('configmap', name)
            emit(configmap=name, uid=cm['metadata']['uid'], immutable=cm.get('immutable', False),
                 data_keys=sorted(cm.get('data', {})),
                 nginx_conf_sha256=m.sha(cm['data']['nginx.conf'].encode()) if isinstance(cm.get('data', {}).get('nginx.conf'), str) else None)
        stage = 'RUNTIME_READ_AND_IN_MEMORY_PATCH_CHECK'
        # Inventory file metadata before applying the previous tool's stricter
        # read gate, so a different runtime UID can be diagnosed safely.
        meta = m.execute('test -f /tmp/nginx.conf; test ! -L /tmp/nginx.conf; stat -c "%u:%g:%a:%h:%s" /tmp/nginx.conf').decode().strip()
        require(re.fullmatch(r'\d+:\d+:[0-7]+:\d+:\d+', meta), 'unexpected_runtime_metadata')
        uid, gid, mode, links, size = meta.split(':')
        emit(runtime_file='/tmp/nginx.conf', uid=int(uid), gid=int(gid), mode=mode,
             links=int(links), bytes=int(size))
        masters = m.execute('for kb_p in /proc/[0-9]*/cmdline; do test -r "$kb_p" || continue; kb_line=$(tr "\\000" " " < "$kb_p" 2>/dev/null) || continue; case "$kb_line" in "nginx: master process "*) printf "%s\\n" "$kb_line";; esac; done').decode().splitlines()
        emit(nginx_master_count=len(masters),
             master_uses_tmp_config=len(masters) == 1 and bool(re.search(r'(?:^|\s)-c\s+/tmp/nginx\.conf(?:\s|$)', masters[0])),
             raw_process_arguments_printed=False)
        runtime = m.live_read()
        root = m.parse_blocks(runtime.decode('utf-8'))
        http = [x for x in root['children'] if x['header'] == ['http']]
        require(len(http) == 1, 'http_block_count_changed')
        shape = []
        for server in http[0]['children']:
            if server['header'] != ['server']:
                continue
            locations = [x for x in server['children'] if x['header'] == ['location', '/']]
            shape.append({'listen': [d[1:] for d in server['directives'] if d and d[0] == 'listen'],
                          'root_location_count': len(locations),
                          'root_proxy_supported': all(any(d in (['proxy_pass', 'http://frontend:3000'], ['proxy_pass', 'http://$frontend_upstream']) for d in loc['directives']) for loc in locations),
                          'root_sub_filter_present': any(d and d[0].startswith('sub_filter') for loc in locations for d in loc['directives'])})
        try:
            candidate = m.patch(runtime)
            transform = {'in_memory_patch': 'PASS', 'candidate_sha256': m.sha(candidate)}
        except m.CheckFailure as exc:
            transform = {'in_memory_patch': 'NEEDS_REVIEW', 'detail': str(exc)}
        emit(runtime_path=m.LIVE, runtime_sha256=m.sha(runtime), runtime_bytes=len(runtime),
             server_shapes=shape, **transform)
        # No nginx -t/reload, staging, or public HTTP requests are made here.
        emit(active_baseline='COMPLETE', production_mutation=False, files_modified=False,
             service_selectors_changed=False, nginx_reload=False, raw_config_printed=False)
        return 0
    except Exception as exc:
        emit(active_baseline='INCOMPLETE', stage=stage, error_type=type(exc).__name__,
             detail=str(exc) if isinstance(exc, AuditFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
             production_mutation=False, files_modified=False, nginx_reload=False,
             raw_error_printed=False, automatic_retry=False)
        return 1


if __name__ == '__main__':
    sys.exit(main())
