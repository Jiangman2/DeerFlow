"""Bounded, read-only evidence for the two operator-supplied OA threads.

Python 3.7 host compatible. Does not run application startup/migrations, read
document bodies, replay jobs, or transmit callbacks. DB transaction is read-only.
"""
import argparse
import json
import subprocess

CHILD = r'''
import asyncio, contextlib, hashlib, io, json, pathlib
async def inspect():
    import asyncpg
    from deerflow.config import get_app_config
    cfg = get_app_config().database
    assert cfg.backend == 'postgres', 'POSTGRES_REQUIRED'
    url = cfg.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1)
    conn = await asyncpg.connect(url, timeout=10, command_timeout=15)
    result = {'cases': [], 'schema_revisions': [], 'source_sha256': {}}
    try:
        async with conn.transaction(readonly=True):
            result['schema_revisions'] = [r[0] for r in await conn.fetch('SELECT version_num FROM alembic_version LIMIT 8')]
            for thread in ('oa-b86f9ae1-aadb-4596-a51e-fe96193bb365', 'oa-03729777-db68-4f44-a4b0-49505b7fa0a5'):
                job = await conn.fetchrow('SELECT id,thread_id,run_id,status,callback_status,process_attempts,callback_attempts,last_error_code,last_callback_status_code,created_at,updated_at,completed_at,delivered_at,output_sha256 FROM oa_policy_flow_jobs WHERE thread_id=$1', thread)
                row = {'thread_id': thread, 'job': dict(job) if job else None, 'runs': []}
                runs = await conn.fetch('SELECT run_id,status,stop_reason,error,created_at,updated_at FROM runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 12', thread)
                for run in runs:
                    data = dict(run); error = data.pop('error') or ''
                    data['error_present'] = bool(error)
                    data['error_sha256'] = hashlib.sha256(error.encode()).hexdigest() if error else None
                    data['error_categories'] = [s for s in ('GraphRecursionError','TimeoutError','CancelledError','InternalServerError','RateLimitError','APIConnectionError','PermissionError','FileNotFoundError','unfinished','recursion','checkpoint','ConnectionError','tool_calls','context length','503','502','429') if s.lower() in error.lower()]
                    data['oa_bound_run'] = bool(job and run['run_id'] == job['run_id'])
                    row['runs'].append(data)
                result['cases'].append(row)
    finally:
        await conn.close()
    root = pathlib.Path('/app/backend')
    for name in ('app/gateway/oa_policy_flow_service.py','app/gateway/routers/oa_policy_flow.py','packages/harness/deerflow/runtime/runs/worker.py'):
        p = root / name
        if p.is_file():
            b = p.read_bytes(); assert len(b) < 1048576
            result['source_sha256'][name] = hashlib.sha256(b).hexdigest()
    return result
sink = io.StringIO()
with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    result = asyncio.run(inspect())
print(json.dumps(result, default=str, sort_keys=True))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args()
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']

    def query(args, timeout=25, limit=8388608):
        p = subprocess.run(base + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if p.returncode or len(p.stdout) > limit:
            raise RuntimeError('QUERY_FAILED')
        return json.loads(p.stdout)

    dep = query(['get', 'deployment', 'gateway', '-o', 'json'])
    assert dep['metadata']['uid'] == 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
    assert dep['metadata']['generation'] == 382
    expected = 'deerflow.local/deer-flow-gateway@sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5'
    pods = query(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']
    eligible = [p for p in pods if not p['metadata'].get('deletionTimestamp') and any(c['name'] == 'gateway' and c['image'] == expected for c in p['spec']['containers']) and any(c['name'] == 'gateway' and c.get('ready') for c in p['status'].get('containerStatuses', []))]
    assert eligible, 'NO_READY_BASELINE_POD'
    pod = sorted(eligible, key=lambda p: p['metadata']['name'])[0]
    name, uid = pod['metadata']['name'], pod['metadata']['uid']
    observation = query(['exec', name, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD], timeout=100, limit=65536)
    after = query(['get', 'pod', name, '-o', 'json'])
    assert after['metadata']['uid'] == uid, 'POD_CHANGED'
    print(json.dumps({'schema': 'portal-oa-case-diagnosis.v1', 'status': 'READONLY_CASES_COLLECTED', 'sample': {'pod': name, 'uid': uid}, 'observation': observation, 'workload_modified': False, 'database_modified': False, 'credentials_exported': False, 'provider_requests': 0, 'callback_requests': 0}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_errors_exported': False, 'workload_modified': False}))
        raise SystemExit(1)
