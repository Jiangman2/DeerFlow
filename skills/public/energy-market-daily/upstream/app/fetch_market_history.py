"""Fetch dated JKX observations, preserving missing days and unit changes."""
import argparse
import calendar
import concurrent.futures
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs
import requests
from fetch_energy_data import SPECS

ROOT = Path(__file__).resolve().parent
FREIGHT = ['LPGD59','LPGD60','LPG1225','LPG1224','LPG1179','LPG1177','LPG1227','LPG1226']

def write_json(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

def endpoint():
    configured=os.environ.get('JKX_API_URL')
    if configured:
        parts=urlsplit(configured)
        access_value=parse_qs(parts.query).get('p',[None])[0]
        address=parts._replace(query='',fragment='').geturl()
    else:
        address=os.environ.get('JKX_API_ENDPOINT','http://ip91.jkx.com.cn:6688/jkx_eecc.aspx')
        access_value=os.environ.get('JKX_API_PASSWORD')
    if not access_value: raise ValueError('Set JKX_API_PASSWORD or JKX_API_URL in the agent secret store')
    return address, access_value


def normalize(payload, day):
    if str(payload.get('errcode'))!='0': raise ValueError('Provider error')
    records=[];seen=set()
    for row in payload['data']:
        code=row['code_name']
        if code in seen: raise ValueError('Duplicate code')
        seen.add(code)
        date=dt.datetime.strptime(row['value_date'].replace('/','-'),'%Y-%m-%d').date().isoformat()
        if date!=day: raise ValueError('Provider returned a different date')
        raw=str(row['close']).strip()
        try:
            value=float(raw)
            if not math.isfinite(value) or value<=0: raise ValueError('Invalid numeric value')
            status='available'
        except ValueError:
            if raw in ['周六','周日','休市','节假日','假日']:status='non_trading'
            elif raw in ['', '-', '--', '暂无', '暂无数据', '未更新']:status='missing'
            else:raise ValueError('Unrecognized quote value')
            value=None
        if code in SPECS and row['unit']!=SPECS[code][2]:raise ValueError('Energy unit mismatch')
        records.append(dict(code=code,date=date,value=value,status=status,unit=row['unit'],item=row['item'],raw_close=raw))
    if not set(SPECS).union(FREIGHT)<=seen:raise ValueError('Incomplete series set')
    return records

def fetch(day):
    path=ROOT/'sources'/f'jkx_new_{day}.json'
    if path.exists():payload=json.loads(path.read_text(encoding='utf-8'))
    else:
        url, access_value = endpoint()
        for attempt in range(2):
            try:
                response=requests.get(url,params={'p':access_value,'pdate':day},timeout=30)
                response.raise_for_status();payload=response.json()
                break
            except Exception:
                if attempt == 1:raise ValueError(f'{day}: API request failed (endpoint redacted)') from None
    records=normalize(payload,day)
    if not path.exists():write_json(path,payload)
    return records

def build(start,end,records):
    result=dict(data_date=end,start=start,end=end,retrieved_at=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),oil=[],gas=[],freight=[],history=dict(start=start,end=end,single_day=False),observations=records)
    for code in list(SPECS)+FREIGHT:
        rows=sorted([r for r in records if r['code']==code],key=lambda r:r['date'])
        current=next(r for r in rows if r['date']==end)
        if current['value'] is None:raise ValueError(f'{code}: target date has no quote')
        kind,name,unit=SPECS[code] if code in SPECS else ('freight',current['item'],current['unit'])
        previous=next((r for r in reversed(rows) if r['date']<end and r['value'] is not None),None)
        same=previous and previous['unit']==unit
        q=dict(name=name,code=code,unit=unit,date=end,value=current['value'],change=(current['value']/previous['value']-1)*100 if same else None,
               previous_date=previous['date'] if same else None,previous_value=previous['value'] if same else None,
               source='金凯讯',contract=current['item'],basis='接口close；名称及单位沿用接口',decimals=3 if kind=='gas' else 2,
               change_method='calculated' if same else 'unit_changed_or_missing_previous',status='available')
        result[kind].append(q)
        # Separate unit regimes, never connect a line across a unit change.
        segments=[];segment=None
        for r in rows:
            if r['date']<start:continue
            if segment is None or segment['unit']!=r['unit']:
                segment=dict(name=name,code=code,unit=r['unit'],axis='right' if code=='NY0421' else 'left',dates=[],values=[],break_before=[])
                segments.append(segment)
            if r['status']=='missing':
                segment['break_before'].append(len(segment['dates']))
            if r['value'] is not None:
                segment['dates'].append(r['date']);segment['values'].append(r['value'])
        result['history'].setdefault(kind,[]).extend(s for s in segments if s['dates'])
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--date',required=True);parser.add_argument('--start')
    args=parser.parse_args();end=dt.date.fromisoformat(args.date)
    month=end.year*12+end.month-1-3;y,m=divmod(month,12)
    start=dt.date.fromisoformat(args.start) if args.start else dt.date(y,m+1,min(end.day,calendar.monthrange(y,m+1)[1]))
    if start>end:raise ValueError('Invalid range')
    days=[(start+dt.timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    records=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        for i,rows in enumerate(pool.map(fetch,days),1):
            records.extend(rows)
            if i%15==0:print(f'Validated {i}/{len(days)} dates',flush=True)
    result=build(start.isoformat(),end.isoformat(),records)
    path=ROOT/'data'/f'energy_{end.isoformat()}.json';write_json(path,result)
    print(f'Complete: {len(days)} days, {len(records)} observations',flush=True)
    for code in list(SPECS)+FREIGHT:
        r=[x for x in records if x['code']==code]
        print(code, 'valid',sum(x['value'] is not None for x in r),'units',sorted(set(x['unit'] for x in r)))

if __name__=='__main__':main()
