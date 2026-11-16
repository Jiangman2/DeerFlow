"""Archive operator evidence and a bounded factual two-task comparison."""
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
SOURCE=Path('C:/Users/15285/Downloads/log(28).txt')
RELEASE=ROOT/'release/energy-two-task-comparison-20261116-r1'
THREADS=['9214a4fc-4cff-4599-9855-ea0af1d1ee27','945b81c9-225f-4a12-a033-19d13da92903']


def canonical(value):return (json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode('utf-8')


def main():
    raw=SOURCE.read_bytes()
    rows=[json.loads(line) for line in raw.decode('utf-8-sig').splitlines() if line.startswith('{')]
    report=next(row for row in rows if row.get('schema')=='energy-two-result-comparison.v1')
    assert report['thread_ids']==THREADS and report['status']=='TWO_NAMED_TASK_RESULTS_OBSERVED_NO_CHANGE'
    assert report['workload_modified'] is False and report['task_retry_requested'] is False and report['work_messages_sent']==0
    assert [row['thread_id'] for row in report['observation']['threads']]==THREADS
    summary={'schema':'energy-two-result-field-comparison.v1','status':'NEWS_IMPORT_FAILURE_AND_DISTINCT_NOTIFICATION_CONFIGURATION_CONFIRMED',
             'source_sha256':hashlib.sha256(raw).hexdigest(),'gateway_at_inspection':report['gateway'],
             'scheduler_config_snapshot':report['observation']['fresh_scheduler_for_selected_pod'],
             'source_of_production_evidence':'OPERATOR_SUPPLIED_READONLY_DIAGNOSTIC',
             'task_or_configuration_changed':False,'tasks':[]}
    for task in report['observation']['threads']:
        run=task['runs'][0];occurrence=task['schedule_occurrences'][0]
        events=task['latest_run_events'];timeline=events['timeline']
        assert events['all_selected_kind_events_observed'] is True and events['content_coverage_complete'] is True
        failed=[{'seq':row['seq'],'error_codes':row['receipt']['error_codes']} for row in timeline if row.get('receipt',{}).get('error_codes')]
        build=[fact for row in timeline if 'build' in row.get('actions',[]) for fact in row.get('stage_facts',[])]
        notification=task['work_notification']
        summary['tasks'].append({'thread_id':task['thread_id'],'platform_run_status':run['status'],
            'queue_wait_seconds':round((dt.datetime.fromisoformat(occurrence['started_at'])-dt.datetime.fromisoformat(occurrence['created_at'])).total_seconds()),
            'run_duration_seconds':run['duration_between_created_updated_seconds'],
            'initial_prepare':[fact for row in timeline if 'prepare' in row.get('actions',[]) for fact in row.get('stage_facts',[])],
            'observed_error_receipts':failed,'build_receipts':build,
            'successful_present_files_receipts':sum(row.get('tool')=='present_files' and row.get('tool_status')=='success' for row in timeline),
            'native_notification_configured':notification['occurrences'][0]['native_notification_configured'],
            'notification_outbox_statuses':[row['status'] for row in notification['outbox']],
            'raw_news_or_commands_exported':False})
    first,second=summary['tasks']
    assert first['build_receipts']==[] and first['successful_present_files_receipts']==0
    assert first['native_notification_configured'] is False and first['notification_outbox_statuses']==[]
    assert [code for row in first['observed_error_receipts'] for code in row['error_codes']]==[
        'NEWS_ORIGINAL_BODY_UNAVAILABLE','NEWS_SOURCE_URL_INVALID','NEWS_TIME_INVALID','NEWS_TIME_INVALID','NEWS_TIME_INVALID']
    assert len(second['build_receipts'])==1 and second['build_receipts'][0]['status']=='REPORT_ACCEPTED'
    assert second['build_receipts'][0]['file_extensions_in_receipt']==['.png','.html','.json','.txt']
    assert second['successful_present_files_receipts']==1 and second['notification_outbox_statuses']==['sent']
    for name,data in [('OPERATOR-OUTPUT.txt',raw),('FIELD-OBSERVATION.json',canonical(report)),('FIELD-COMPARISON.json',canonical(summary))]:
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==data,'Historical observation changed'
        else:path.write_bytes(data)
    print(json.dumps({'status':summary['status'],'gateway_generation':report['gateway']['generation'],
                      'news_import_failures':len(first['observed_error_receipts']),'successful_png_seconds':second['build_receipts'][0]['render_seconds'],
                      'successful_notification_status':'sent','production_changes':0},sort_keys=True))


if __name__=='__main__':main()
