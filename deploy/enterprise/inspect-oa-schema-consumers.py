"""Read-only schema consumer inventory, no database connections."""
import contextlib
import io
import json
import subprocess
import argparse

CHILD = r'''
import contextlib,io,json,pathlib,hashlib,os
from urllib.parse import urlsplit
sink=io.StringIO()
with contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
 from deerflow.config import get_app_config
 from deerflow.persistence.bootstrap import _get_head_revision
 c=get_app_config().database
 u=urlsplit(c.postgres_url.replace('postgresql+asyncpg://','postgresql://',1)) if c.backend=='postgres' else None
 endpoint=[u.hostname,u.port or 5432,u.path] if u else None
 head=_get_head_revision()
p=pathlib.Path('/app/backend/app/gateway/deps.py')
b=p.read_bytes(); assert len(b)<262144
print(json.dumps({'schema_head':head,'database_backend':c.backend,'database_endpoint_fingerprint':hashlib.sha256(json.dumps(endpoint,sort_keys=True).encode()).hexdigest() if endpoint else None,'runtime_profile':os.getenv('DEER_FLOW_RUNTIME_PROFILE',''),'deps_sha256':hashlib.sha256(b).hexdigest(),'startup_engine_call_present':b'await init_engine_from_config(config.database)' in b,'database_access':False,'credentials_exported':False}))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
    def query(args):
        p = subprocess.run(base + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        assert p.returncode == 0 and len(p.stdout) < 2097152, 'QUERY_FAILED'
        return json.loads(p.stdout)
    rows = []
    for name in ('gateway', 'ppt-master-runner'):
        before = query(['get', 'deployment', name, '-o', 'json'])
        observation = query(['exec', 'deployment/' + name, '--', '/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD])
        after = query(['get', 'deployment', name, '-o', 'json'])
        assert before['metadata']['uid'] == after['metadata']['uid'] and before['spec'] == after['spec'], 'DEPLOYMENT_CHANGED'
        rows.append({'deployment': name, 'uid': before['metadata']['uid'], 'generation': before['metadata']['generation'], 'images': [c['image'] for c in before['spec']['template']['spec']['containers']], 'observation': observation})
    fingerprints = [r['observation']['database_endpoint_fingerprint'] for r in rows]
    print(json.dumps({'schema':'portal-oa-schema-consumers.v1','status':'READONLY_CONSUMERS_INSPECTED','consumers':rows,'same_database_endpoint':bool(fingerprints[0] and fingerprints[0] == fingerprints[1]),'database_access':False,'workload_modified':False},sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status':'INCOMPLETE','error_class':type(exc).__name__,'raw_errors_exported':False,'workload_modified':False}))
        raise SystemExit(1)
