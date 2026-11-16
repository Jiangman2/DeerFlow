"""Preserve the supplied r2 receipt without replaying production operations."""
import datetime
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
RELEASE=REPO/'release/energy-reader-work-repair-20261115-r2'
INPUT=Path('C:/Users/15285/.codex/attachments/ee7b17fc-1f90-4344-a8f5-157f2f75aeb0/Pasted text.txt')

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(name,raw):
    path=RELEASE/name
    if path.exists():assert path.read_bytes()==raw,'ARCHIVED_EVIDENCE_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT'
    else:path.write_bytes(raw)

def main():
    original=INPUT.read_bytes();rows=[]
    for line in original.decode('utf-8-sig').splitlines():
        if line.startswith('{'):rows.append(json.loads(line))
    results=[row for row in rows if row.get('schema')=='energy-reader-work-repair.v1']
    assert len(results)==1
    result=results[0]
    assert result['status']=='SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED'
    assert result['operation']=='0a8ffbe517904536bbdda5fc3d0f4c96'
    assert result['all_gateway_pods_verified'] is True and result['actual_serving_nginx_verified'] is True
    assert result['gateway_patch_requests']==result['nginx_patch_requests']==1 and result['configmap_create_requests']==2
    assert result['once_claimed'] is True and result['business_acceptance_pending'] is True
    gw,ng=result['gateway'],result['nginx']
    assert gw['generation']==gw['observedGeneration']==418
    assert all(gw[key]==70 for key in ('desired','replicas','readyReplicas','updatedReplicas','availableReplicas','verified_pods'))
    assert gw['terminatingReplicas']==0 and gw['source_files_per_pod']==14
    assert gw['service_backends_verified'] is True and gw['loaded_application_modules_verified'] is False
    assert gw['spec_sha256']=='ebdfb3f85c151a67b12c6aed4f145b60330e19925996194f201f31d839ee0c0e'
    assert ng['generation']==ng['observedGeneration']==3 and ng['verified_pods']==1 and ng['service_backends_verified'] is True
    assert ng['spec_sha256']=='6061194b0c497f516b8b6c55e7f74f435bca5effc378d7f097c8b2d67d0dcd72'
    assert ng['actual_serving_route_verified'] is True and ng['skillhub_auth_preserved'] is True
    readers=[row for row in ng['http_checks'] if row['path'].startswith('/reports/energy-daily/')]
    assert len(readers)==2 and {row['host'] for row in readers}=={'cnoocclaw.cnooc.com.cn','heai.cnooc.com.cn'}
    assert all(row['http_status']==302 and row['exact_login_return_verified'] is True and row['credentials_used'] is False for row in readers)
    assert result['scheduler_recursion_limit']==result['max_recursion_limit']==10000
    assert result['work_messages_sent']==result['provider_requests']==result['secret_api_reads']==0
    assert result['skill_modified'] is False and result['schedules_modified'] is False and result['existing_runs_replayed'] is False
    field=json.loads((HERE/'FIELD-BINDING.json').read_bytes())
    assert result['qualification']['sources']==field['gateway_parent_sources']
    assert result['qualification']['config_sha256']==field['gateway_config_sha256']
    assert result['qualification']['synthetic_checks']==12
    reconstructed={k:v for k,v in result.items() if k not in ('report_bytes','report_path','report_sha256')}
    raw=canonical(reconstructed)
    assert len(raw)==result['report_bytes']==5820
    assert sha(raw)==result['report_sha256']=='bae5e21202545028c175e20e73ce651cb1018d7850368aeb4cd5bbde4813c1ba'
    source=json.loads((RELEASE/'SOURCE.json').read_bytes())
    script=RELEASE/source['filename'];assert sha(script.read_bytes())==source['sha256']
    observed=datetime.datetime.fromisoformat(result['observed_at']).astimezone(datetime.timezone(datetime.timedelta(hours=8)))
    summary={'schema':'energy-reader-work-repair-operator-acceptance.v1','status':result['status'],'operation':result['operation'],
             'observed_at_asia_shanghai':observed.isoformat(),'operator_output_bytes':len(original),'operator_output_sha256':sha(original),
             'reconstructed_report_bytes':len(raw),'reconstructed_report_sha256':sha(raw),'qualified_script_sha256':source['sha256'],
             'gateway_generation':418,'gateway_verified_pods':70,'source_files_per_pod':14,'serving_nginx_generation':3,
             'actual_service_backends_verified':True,'anonymous_login_redirect_verified':True,'logged_in_reading_verified':False,
             'real_scheduled_work_notification_verified':False,'business_acceptance_pending':True,'work_messages_sent_by_installer':0,
             'skill_modified':False,'schedules_modified':False,'provider_requests':0,'loaded_application_modules_verified':False}
    save('OPERATOR-OUTPUT.txt',original)
    save('OPERATOR-FINAL.json',(json.dumps(result,sort_keys=True,indent=2)+'\n').encode())
    save('REPORT-RECONSTRUCTED.json',raw)
    save('OPERATOR-ACCEPTANCE.json',(json.dumps(summary,sort_keys=True,indent=2)+'\n').encode())
    print(json.dumps(summary,sort_keys=True))

if __name__=='__main__':main()
