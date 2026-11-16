"""Archive the operator's read-only result and bind only the verified targets."""
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
INPUT=Path('C:/Users/15285/.codex/attachments/59e85225-4ad8-4d36-a3f0-37006950e280/Pasted text.txt')
RELEASE=REPO/'release/energy-reader-serving-diagnostic-20261115-r1'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(path,raw):
    if path.exists():assert path.read_bytes()==raw,'EXISTING_EVIDENCE_DIFFERS'
    else:path.write_bytes(raw)

def main():
    raw=INPUT.read_bytes()
    rows=[json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.startswith('{"credentials_exported"')]
    assert len(rows)==1
    report=rows[0];edge=report['edge'];active=edge['serving_workloads'][0];cm=active['configmaps'][0]
    assert report['status']=='READONLY_DIAGNOSTICS_COLLECTED' and edge['all_service_endpoints_rechecked'] is True
    assert active['deployment']=='nginx-security-heai-20261012-r1' and active['anonymous_route_probe']['http_status']==404
    assert len(edge['serving_workloads'])==1 and len(cm['files'])==1 and cm['files'][0]['key']=='nginx.conf'
    assert report['views']['all_sampled_views_match'] is True
    thread=report['task']['observation']['threads'][0]
    assert thread['thread_id']=='d934a54e-b894-4970-a32e-9b983c55d0df' and thread['report_outputs']['requested_flat_html_present'] is True
    assert thread['schedule_tasks'][0]['native_work_notification_configured'] is True
    evidence=thread['latest_run_events']['reader_and_send_evidence']
    assert evidence[-1]['reader_label_present'] is True and evidence[-1]['reader_urls']
    assert report['task']['gateway']['generation']==417
    parent=(HERE.parent/'task-conversation-context-20261114-r1/activate-task-conversation-context-20261114-r1.py').read_text(encoding='utf-8')
    expected=ast.literal_eval(next(n.value for n in ast.parse(parent).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EXPECTED_SOURCES' for t in n.targets)))
    expected['/app/backend/packages/harness/deerflow/sandbox/tools.py']={'bytes':100988,'sha256':'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330'}
    assert len(expected)==14
    for name in ('work_notification_delivery.py','work_notifications.py'):
        key='app/scheduler/'+name
        raw_parent=(REPO/'portable-skills/deployment/gateway-link-r3/backend'/key).read_bytes()
        observed=report['task']['observation']['source_hashes'][key]
        assert observed=={'bytes':len(raw_parent),'sha256':sha(raw_parent)}
        assert expected['/app/backend/'+key]==observed
    binding={'schema':'energy-reader-link-repair-field-binding.v1','diagnostic_sha256':sha(canonical(report)),
             'diagnostic_observed_at':report['task']['observed_at'],'services':edge['services'],'nginx':active,
             'gateway':report['task']['gateway'],'gateway_mount_metadata':report['task']['gateway_mount_metadata'],
             'gateway_parent_sources':expected,'gateway_config_sha256':'b160057a087d848310f4521162fbee07f25178812692c3b1ec253be3632b08bf',
             'skill_tree_sha256':report['views']['expected_skill_tree_sha256'],'skill_already_visible':True,
             'thread_id':thread['thread_id'],'publication_date':'2026-11-15','native_work_notification_confirmed':True,
             'reported_file_exists_and_final_response_contains_reader_url':True,
             'scheduled_hour_observed_asia_shanghai':6,'schedule_mutation_authorized_by_this_repair':False}
    save(RELEASE/'OPERATOR-OUTPUT.txt',raw)
    save(RELEASE/'OPERATOR-DIAGNOSTIC.json',canonical(report))
    save(HERE/'FIELD-BINDING.json',(json.dumps(binding,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps({'diagnostic_sha256':binding['diagnostic_sha256'],'active_nginx_generation':active['generation'],
                      'active_nginx':active['deployment'],'gateway_parent_source_files':len(expected),'skill_visible':True,'native_work_confirmed':True}))

if __name__=='__main__':main()
