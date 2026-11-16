"""Read only the current Nginx configuration contract; never export its body."""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import types

READER_SOURCE = ''
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


class Refused(ValueError):
    pass


def need(condition, code):
    if not condition:
        raise Refused(code)


class Inspector:
    def __init__(self):
        self.configmaps = set()
        self.queries = 0

    def query(self, args):
        allowed = args == ['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json']
        if len(args) == 5 and args[:2] == ['get', 'configmap'] and args[2] in self.configmaps and args[3:] == ['-o', 'json']:
            allowed = True
        need(allowed, 'FIXED_READONLY_COMMAND_REQUIRED')
        self.queries += 1
        result = subprocess.run(KUBE + args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=25, check=False, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'})
        need(result.returncode == 0, 'READONLY_KUBECTL_FAILED')
        need(len(result.stdout) <= 4194304 and len(result.stderr) <= 65536, 'READONLY_OUTPUT_BOUND')
        return json.loads(result.stdout)

    def run(self, reader):
        rows = self.query(['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json'])['items']
        by_name = {row['metadata']['name']: row for row in rows}
        need(set(by_name) == {'nginx', 'gateway', 'frontend'}, 'DEPLOYMENT_SET_REQUIRED')
        summaries = {}
        for name, row in by_name.items():
            summaries[name] = {'uid': row['metadata']['uid'], 'generation': row['metadata']['generation'],
                               'spec_sha256': digest(canonical(row['spec'])),
                               'images': {c['name']: c['image'] for c in row['spec']['template']['spec']['containers']},
                               'desired': row['spec'].get('replicas', 1),
                               'updated': row.get('status', {}).get('updatedReplicas', 0),
                               'ready': row.get('status', {}).get('readyReplicas', 0),
                               'available': row.get('status', {}).get('availableReplicas', 0)}
        nginx = by_name['nginx']
        volumes = nginx['spec']['template']['spec'].get('volumes', [])
        sources = {v['name']: v['configMap']['name'] for v in volumes if isinstance(v.get('configMap'), dict)}
        need(0 < len(sources) <= 8, 'NGINX_CONFIGMAP_MOUNTS_REQUIRED')
        self.configmaps = set(sources.values())
        need(all(re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', value) for value in self.configmaps), 'CONFIGMAP_NAME_INVALID')
        configs = []
        for name in sorted(self.configmaps):
            cm = self.query(['get', 'configmap', name, '-o', 'json'])
            data = cm.get('data', {})
            need(isinstance(data, dict) and len(data) <= 32, 'CONFIGMAP_DATA_BOUND')
            entry = {'name': name, 'uid': cm['metadata']['uid'], 'resource_version': cm['metadata']['resourceVersion'],
                     'immutable': cm.get('immutable', False), 'data_sha256': digest(canonical(data)), 'files': []}
            for key, source in sorted(data.items()):
                need(isinstance(source, str) and len(source.encode()) <= 2097152, 'CONFIGURATION_SOURCE_BOUND')
                item = {'key': key, 'bytes': len(source.encode()), 'sha256': digest(source.encode())}
                if 'nginx' in key or key.endswith('.conf'):
                    try:
                        item['reader_contract'] = reader.inspect(source)
                    except Exception as exc:
                        item['analysis_error_class'] = type(exc).__name__
                        if isinstance(exc, reader.Refused):
                            item['analysis_error_code'] = str(exc)
                entry['files'].append(item)
            configs.append(entry)
        mounts = [{'container': c['name'], 'volume': m['name'], 'mount_path': m['mountPath'],
                   'subpath': m.get('subPath'), 'read_only': m.get('readOnly', False),
                   'configmap': sources[m['name']]}
                  for c in nginx['spec']['template']['spec']['containers']
                  for m in c.get('volumeMounts', []) if m['name'] in sources]
        fresh = self.query(['get', 'deployments', 'nginx', 'gateway', 'frontend', '-o', 'json'])['items']
        stable = {r['metadata']['name']: digest(canonical(r['spec'])) for r in fresh} == {n: s['spec_sha256'] for n, s in summaries.items()}
        return {'deployments': summaries, 'nginx_configmaps': configs, 'nginx_config_mounts': mounts,
                'stable_spec_readback': stable,
                'status': 'NGINX_READER_PREFLIGHT_OBSERVED_NO_CHANGE' if stable else 'DEPLOYMENT_SPEC_CHANGED_DURING_READ'}


def main():
    out = {'schema': 'energy-html-reader-preflight.v1', 'status': 'INCOMPLETE', 'workload_modified': False,
           'persistent_files_modified': False, 'credentials_exported': False, 'secret_api_reads': 0,
           'provider_requests': 0, 'work_messages_sent': 0, 'configuration_body_exported': False}
    inspector = None
    try:
        need(sys.argv[1:] == ['--inspect-readonly'], 'READONLY_FLAG_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        need(bool(READER_SOURCE), 'INSPECTOR_NOT_SEALED')
        reader = types.ModuleType('_energy_nginx_reader')
        sys.modules[reader.__name__] = reader
        exec(compile(READER_SOURCE, '<sealed-nginx-contract>', 'exec'), reader.__dict__)
        inspector = Inspector()
        out.update(inspector.run(reader))
    except Exception as exc:
        out.update(error_class=type(exc).__name__)
        if isinstance(exc, Refused):
            out['error_code'] = str(exc)
    out.update(queries=inspector.queries if inspector else 0, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
