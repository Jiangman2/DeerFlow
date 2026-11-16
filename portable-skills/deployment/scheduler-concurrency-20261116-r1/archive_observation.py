"""Archive the operator's queue snapshot and its qualified resource projection."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SOURCE=Path('C:/Users/15285/.codex/attachments/c9e1bd3d-2389-4f42-a4f6-303cb64fc75a/已粘贴的文本.txt')
RELEASE=ROOT/'release/scheduler-concurrency-20261116-r1'
raw=SOURCE.read_bytes()
rows=[json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.startswith('{')]
result=next(row for row in rows if row.get('schema')=='scheduler-queue-inspection.v1')
assert result['status']=='SCHEDULER_QUEUE_METADATA_OBSERVED_NO_CHANGE'
observation=result['observation']
assert observation['fresh_config_scheduler']['max_concurrent_runs']==3
assert {row['status']:row['count'] for row in observation['queue_counts']}=={'running':3,'queued':86}
assert all(row['run_lease_state']=='NOT_EXPIRED' for row in observation['executing_rows'])
assert len(observation['targets'])==2 and all(row['run_count']==0 for row in observation['targets'])
assert result['gateway']['generation']==418 and result['gateway_replicas']['readyReplicas']==70
assert result['workload_modified'] is False and result['database_modified'] is False
summary={'schema':'scheduler-queue-field-observation.v1','status':'CAPACITY_THREE_SATURATED_WITH_86_WAITING',
         'observed_at_utc':observation['database_observed_at_utc'],'source_attachment_sha256':hashlib.sha256(raw).hexdigest(),
         'gateway':result['gateway'],'configured_global_concurrency':3,'running':3,'queued':86,
         'running_leases_not_expired':3,'targets_not_started':2,'multi_instance_configured':False,
         'scheduler_step_budget':10000,'requested_final_concurrency':32,
         'source_hashes':observation['source_hashes'],'read_only':True,'production_update_performed':False}
RELEASE.mkdir(parents=True,exist_ok=True)
for name,data in [('OPERATOR-QUEUE-OUTPUT.txt',raw),('FIELD-OBSERVATION.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode()),
                  ('FIELD-SUMMARY.json',(json.dumps(summary,sort_keys=True,indent=2)+'\n').encode())]:
    path=RELEASE/name
    if path.exists():assert path.read_bytes()==data,'Historical observation already exists with different content'
    else:path.write_bytes(data)
print(json.dumps(summary,sort_keys=True))
