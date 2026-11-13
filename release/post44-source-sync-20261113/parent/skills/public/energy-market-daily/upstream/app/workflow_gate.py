"""Portable scheduling gate, API probe and per-edition completion bookkeeping.
No daemon or scheduler is created by this module.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import uuid
from pathlib import Path
import requests
from fetch_market_history import endpoint, normalize, write_json
from validate_inputs import validate
ROOT=Path(__file__).resolve().parent
PACKAGE=ROOT.parent
TZ=dt.timezone(dt.timedelta(hours=8),'Asia/Shanghai')
STATE=ROOT/'state'

def paths(day):
    base=ROOT/'output'/f'油气与金融市场日报_{day}'
    return [base.with_suffix(s) for s in ['.html','.png','.json']]
def hashes(day):return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths(day)}
def complete_exists(day):
    marker=STATE/f'{day}.complete.json'
    if not marker.exists():return False
    saved=json.loads(marker.read_text(encoding='utf-8'))
    return all(p.is_file() and p.stat().st_size for p in paths(day)) and saved['outputs']==hashes(day)
def emit(status,**extra):print(json.dumps(dict(status=status,**extra),ensure_ascii=False))
def own(day,owner):
    p=STATE/f'{day}.lock.json'
    if p.exists():
        data=json.loads(p.read_text(encoding='utf-8'))
        if data['owner']!=owner:raise ValueError('Lock belongs to another execution; use its owner identifier')
    return p

def probe():
    config=json.loads((PACKAGE/'config/schedule.json').read_text(encoding='utf-8'))
    if not config['enabled']:emit('PAUSED');return
    now=dt.datetime.now(TZ);day=now.date().isoformat();target=(now.date()-dt.timedelta(days=1)).isoformat()
    weekday=['MO','TU','WE','TH','FR','SA','SU'][now.weekday()]
    if weekday not in config['weekdays'] or not config['start']<=now.strftime('%H:%M')<=config['end']:
        emit('OUTSIDE_WINDOW');return
    if complete_exists(day):emit('COMPLETE',date=day);return
    if (STATE/f'{day}.complete.json').exists():emit('NEEDS_REVIEW',date=day,reason='Completed outputs were changed or removed');return
    if (STATE/f'{day}.lock.json').exists():emit('IN_PROGRESS',date=day);return
    cached=ROOT/'data'/f'energy_{target}.json'
    if cached.exists():
        q=json.loads(cached.read_text(encoding='utf-8'))
        if q.get('data_date')==target and len(q.get('oil',[]))==3 and len(q.get('gas',[]))==3 and len(q.get('freight',[]))==8 and all(x.get('date')==target and isinstance(x.get('value'),(float,int)) and x['value']>0 for k in ['oil','gas','freight'] for x in q[k]):
            emit('READY',date=day,data_date=target);return
    try:
        address,access_value=endpoint()
        response=requests.get(address,params={'p':access_value,'pdate':target},timeout=30)
        response.raise_for_status();payload=response.json();rows=normalize(payload,target)
        if not all(r['value'] is not None for r in rows):
            emit('WAIT_DATA',date=day,reason='Target-day data incomplete');return
    except Exception:
        emit('WAIT_DATA',date=day,reason='API/configuration/validation failed; credentials omitted');return
    write_json(ROOT/'sources'/f'jkx_new_{target}.json',payload)
    emit('HISTORY_REQUIRED',date=day,data_date=target)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['probe','claim','release','complete']);p.add_argument('--date');p.add_argument('--owner');p.add_argument('--force',action='store_true');a=p.parse_args()
    STATE.mkdir(exist_ok=True)
    if a.action=='probe':probe();return
    if not a.date:p.error('--date required')
    dt.date.fromisoformat(a.date);lock=STATE/f'{a.date}.lock.json'
    if a.action=='claim':
        owner=uuid.uuid4().hex
        try:
            with lock.open('x',encoding='utf-8') as f:json.dump(dict(owner=owner,date=a.date,created_at=dt.datetime.now(TZ).isoformat(),pid=os.getpid()),f)
        except FileExistsError:emit('IN_PROGRESS',date=a.date);return
        emit('CLAIMED',date=a.date,owner=owner);return
    if a.action=='release':
        if not a.force:own(a.date,a.owner)
        lock.unlink(missing_ok=True);emit('RELEASED',date=a.date);return
    own(a.date,a.owner);validate(a.date)
    if not all(p.is_file() and p.stat().st_size for p in paths(a.date)):raise ValueError('Output files missing')
    html,png,snapshot=paths(a.date)
    data=json.loads(snapshot.read_text(encoding='utf-8'))
    if data['publication_date']!=a.date or data['data_date']!=(dt.date.fromisoformat(a.date)-dt.timedelta(days=1)).isoformat() or len(data['company_news'])<5:raise ValueError('Output snapshot invalid')
    if png.read_bytes()[:8]!=b'\x89PNG\r\n\x1a\n':raise ValueError('Invalid PNG')
    write_json(STATE/f'{a.date}.complete.json',dict(date=a.date,completed_at=dt.datetime.now(TZ).isoformat(),outputs=hashes(a.date)))
    emit('COMPLETE',date=a.date)

if __name__=='__main__':main()
