"""Bounded, read-only evidence for the two operator-supplied OA threads.

Python 3.7 host compatible. Does not run application startup/migrations, read
document bodies, replay jobs, or transmit callbacks. DB transaction is read-only.
"""
import argparse
import json
import subprocess

CHILD = '\nimport asyncio, contextlib, hashlib, io, json, pathlib\nasync def inspect():\n    import asyncpg\n    from deerflow.config import get_app_config\n    cfg = get_app_config().database\n    assert cfg.backend == \'postgres\', \'POSTGRES_REQUIRED\'\n    url = cfg.postgres_url.replace(\'postgresql+asyncpg://\', \'postgresql://\', 1)\n    conn = await asyncpg.connect(url, timeout=10, command_timeout=15)\n    result = {\'cases\': [], \'schema_revisions\': [], \'source_sha256\': {}}\n    try:\n        async with conn.transaction(readonly=True):\n            result[\'schema_revisions\'] = [r[0] for r in await conn.fetch(\'SELECT version_num FROM alembic_version LIMIT 8\')]\n            for thread in (\'oa-b86f9ae1-aadb-4596-a51e-fe96193bb365\', \'oa-03729777-db68-4f44-a4b0-49505b7fa0a5\'):\n                job = await conn.fetchrow(\'SELECT id,thread_id,run_id,status,callback_status,process_attempts,callback_attempts,last_error_code,last_callback_status_code,created_at,updated_at,completed_at,delivered_at,output_sha256 FROM oa_policy_flow_jobs WHERE thread_id=$1\', thread)\n                row = {\'thread_id\': thread, \'job\': dict(job) if job else None, \'runs\': []}\n                runs = await conn.fetch(\'SELECT run_id,status,stop_reason,error,created_at,updated_at FROM runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 12\', thread)\n                for run in runs:\n                    data = dict(run); error = data.pop(\'error\') or \'\'\n                    data[\'error_present\'] = bool(error)\n                    data[\'error_sha256\'] = hashlib.sha256(error.encode()).hexdigest() if error else None\n                    data[\'error_categories\'] = [s for s in (\'GraphRecursionError\',\'TimeoutError\',\'CancelledError\',\'InternalServerError\',\'RateLimitError\',\'APIConnectionError\',\'PermissionError\',\'FileNotFoundError\',\'unfinished\',\'recursion\',\'checkpoint\',\'ConnectionError\',\'tool_calls\',\'context length\',\'503\',\'502\',\'429\') if s.lower() in error.lower()]\n                    data[\'oa_bound_run\'] = bool(job and run[\'run_id\'] == job[\'run_id\'])\n                    row[\'runs\'].append(data)\n\n                if thread == \'oa-03729777-db68-4f44-a4b0-49505b7fa0a5\' and job:\n                    import re\n                    bound = next((r for r in runs if r[\'run_id\'] == job[\'run_id\']), None)\n                    error = (bound[\'error\'] or \'\') if bound else \'\'\n                    row[\'recursion_limit\'] = (re.findall(r\'recursion limit \\((\\d+)\\)\', error) or [None])[0]\n                    row[\'durable_message_count\'] = (re.findall(r\'contains (\\d+) messages\', error) or [None])[0]\n                    row[\'last_action_names\'] = re.findall(r\'last action: ([A-Za-z0-9_, ]{1,200})\\.\', error)\n                    events = await conn.fetch(\'SELECT seq,event_type,created_at,octet_length(content) AS bytes,CASE WHEN octet_length(content)<=32768 THEN content ELSE NULL END AS content FROM run_events WHERE thread_id=$1 AND run_id=$2 ORDER BY seq DESC LIMIT 80\', thread, job[\'run_id\'])\n                    row[\'recent_events\'] = []\n                    for event in reversed(events):\n                        data = {}; raw = event[\'content\'] or \'\'\n                        try:\n                            parsed = json.loads(raw)\n                            if isinstance(parsed,dict): data = parsed\n                        except (ValueError,TypeError): pass\n                        calls = []\n                        for c in data.get(\'tool_calls\',[]) if isinstance(data.get(\'tool_calls\'),list) else []:\n                            if isinstance(c,dict):\n                                name = c.get(\'name\',\'\')\n                                calls.append({\'tool\':name if re.fullmatch(r\'[A-Za-z0-9_]{1,80}\',name) else \'OTHER\',\'arguments_sha256\':hashlib.sha256(json.dumps(c.get(\'args\'),sort_keys=True).encode()).hexdigest()})\n                        tool_name=data.get(\'name\',\'\')\n                        row[\'recent_events\'].append({\'seq\':event[\'seq\'],\'event_type\':event[\'event_type\'],\'created_at\':event[\'created_at\'],\'bytes\':event[\'bytes\'],\'content_inspected\':event[\'content\'] is not None,\'tool\':tool_name if isinstance(tool_name,str) and re.fullmatch(r\'[A-Za-z0-9_]{1,80}\',tool_name) else None,\'calls\':calls,\'tool_status\':data.get(\'status\') if data.get(\'status\') in (\'success\',\'error\') else None,\'markers\':[v for v in (\'unfinished\',\'present_files\',\'recursion\',\'validation failed\',\'Traceback\',\'loop\',\'timeout\') if v.lower() in raw.lower()]})\n                cp = await conn.fetchrow("SELECT metadata,checkpoint->>\'ts\' AS ts FROM checkpoints WHERE thread_id=$1 AND checkpoint_ns=\'\' ORDER BY checkpoint_id DESC LIMIT 1", thread)\n                if cp:\n                    metadata=json.loads(cp[\'metadata\']) if isinstance(cp[\'metadata\'],str) else cp[\'metadata\']\n                    row[\'latest_checkpoint\']={\'timestamp\':cp[\'ts\'],\'bound_run_metadata_matches\':bool(job and metadata.get(\'deerflow_timing_run_id\')==job[\'run_id\']),\'run_metadata_present\':\'deerflow_timing_run_id\' in metadata}\n\n                result[\'cases\'].append(row)\n    finally:\n        await conn.close()\n    root = pathlib.Path(\'/app/backend\')\n    for name in (\'app/gateway/oa_policy_flow_service.py\',\'app/gateway/routers/oa_policy_flow.py\',\'packages/harness/deerflow/runtime/runs/worker.py\'):\n        p = root / name\n        if p.is_file():\n            b = p.read_bytes(); assert len(b) < 1048576\n            result[\'source_sha256\'][name] = hashlib.sha256(b).hexdigest()\n    return result\nsink = io.StringIO()\nwith contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):\n    result = asyncio.run(inspect())\nprint(json.dumps(result, default=str, sort_keys=True))\n'


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
    print(json.dumps({'schema': 'portal-oa-case-diagnosis.v2', 'status': 'READONLY_CASES_COLLECTED', 'sample': {'pod': name, 'uid': uid}, 'observation': observation, 'workload_modified': False, 'database_modified': False, 'credentials_exported': False, 'provider_requests': 0, 'callback_requests': 0}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'raw_errors_exported': False, 'workload_modified': False}))
        raise SystemExit(1)
