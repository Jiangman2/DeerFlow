"""Render synthetic local samples only; no providers, credentials or publication."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('programmatic_samples',HERE/'test_energy_programmatic_delivery_20261114.py')
tests=importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
daily=tests.daily
root=tests.REPO/'portable-skills/validation/energy-market-daily-2.1.3-20261114'
root.mkdir(parents=True,exist_ok=True)
project=root/'synthetic-project'; project.mkdir(exist_ok=True)
tests.fixtures.project_fixture(project)
data=daily.snapshot(project)
results=[]
for name,missing in (('complete',False),('freight-reference',True)):
    sample=copy.deepcopy(data)
    if missing:
        sample['formal_report_ready']=False; sample['quality']='数据不完整'
        target=dt.date.fromisoformat(sample['data_date'])
        for quote in sample['freight']:
            quote.update(status='latest_available',date=(target-dt.timedelta(days=2)).isoformat(),quote_age_days=2,
                         target_date=target.isoformat(),target_status='missing')
        sample['freight_reference_count']=8; sample['freight_reference_search_complete']=True
    html=root/(name+'.html'); html.write_text(daily.render(sample),encoding='utf-8')
    before=html.read_bytes()
    (root/(name+'.json')).write_text(json.dumps(sample,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/(name+'.txt')).write_text(daily.reply_text(sample),encoding='utf-8')
    png=daily.png(html,sample)
    assert html.read_bytes()==before
    results.append({'name':name,'png':png,'png_bytes':html.with_suffix('.png').stat().st_size,
                    'png_sha256':daily.digest(html.with_suffix('.png').read_bytes()),'html_unchanged':True})
overflow_checks=[]
with tempfile.TemporaryDirectory(prefix='energy-213-overflow-') as temporary:
    for width in (320,390,430):
        html=Path(temporary)/(str(width)+'.html')
        css='@media(width:%dpx){body{min-width:%dpx!important}}' % (width,width+1)
        html.write_text(daily.render(data).replace('</style>',css+'</style>'),encoding='utf-8')
        try:
            daily.png(html,data)
        except tests.Failure as exc:
            assert exc.code=='REPORT_HORIZONTAL_OVERFLOW' and exc.details['width']==width
            overflow_checks.append({'width':width,'status':'REJECTED','png_written':html.with_suffix('.png').exists()})
        else:
            raise AssertionError('overflow report accepted')
report={'schema':'energy-213-local-render.v1','status':'LOCAL_WINDOWS_BROWSER_RENDERED','samples':results,
        'real_browser_overflow_rejections':overflow_checks,
        'production_arm64_qualified':False,'visual_review_pending':True,'provider_requests':0,'credentials_read':False,
        'messages_sent':0,'real_browser_used':True,'sources':'SYNTHETIC_LOCAL_FIXTURE'}
(root/'RENDER.json').write_text(json.dumps(report,sort_keys=True,indent=2),encoding='utf-8')
print(json.dumps(report,sort_keys=True))
