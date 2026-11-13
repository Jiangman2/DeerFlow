"""Validate dated inputs before allowing an agent to publish local artifacts."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
from generate_report import previous_months

ROOT=Path(__file__).resolve().parent
STOCK_CODES=[{'600938.SH','sh600938'},{'00883.HK','hk00883'},{'601857.SH','sh601857'},{'600028.SH','sh600028'},{'000002.SH','sh000002'}]
FX_CODES=[{'DXY','DINIW'},{'USD/CNY','fx_susdcny'}]

def require(ok,message):
    if not ok:raise ValueError(message)
def number(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)
def evidence(q):
    require(bool(q.get('source')) and str(q.get('url','')).startswith(('https://','http://')),'Missing source URL/name')
    require(bool(q.get('retrieved_at')),'Missing retrieval time')
    dt.datetime.fromisoformat(q['retrieved_at'])
    files=q.get('evidence',[]);files=[files] if isinstance(files,str) else files
    require(bool(files),'Missing evidence archive')
    for f in files:
        p=(ROOT/f).resolve()
        require(ROOT.resolve() in p.parents and p.is_file(),'Evidence missing or outside app directory')

def validate(day):
    publication=dt.date.fromisoformat(day);target=publication-dt.timedelta(days=1);t=target.isoformat()
    data={k:json.loads((ROOT/'data'/name).read_text(encoding='utf-8')) for k,name in [('energy',f'energy_{t}.json'),('financial',f'financial_{t}.json'),('news',f'news_{day}.json')]}
    energy=data['energy'];financial=data['financial'];news=data['news']
    require(energy['data_date']==t and financial['data_date']==t and news['publication_date']==day,'Snapshot date mismatch')
    require(energy['history']['start']==previous_months(target,3).isoformat() and energy['history']['end']==t,'History window mismatch')
    for kind,size in [('oil',3),('gas',3),('freight',8)]:
        require(len(energy[kind])==size,'Incomplete API set')
        for q in energy[kind]:
            require(q['date']==t and number(q['value']) and q['value']>0,'Invalid target API quote')
            if q['change'] is not None:
                require(q.get('previous_date','9999')<t and number(q.get('previous_value')) and q['previous_value']>0,'Invalid API previous value')
                require(math.isclose(q['change'],(q['value']/q['previous_value']-1)*100,abs_tol=1e-9),'API change mismatch')
    for kind,codes,units in [('stocks',STOCK_CODES,['元','港元','元','元','点']),('fx',FX_CODES,['点','元/美元'])]:
        require(len(financial[kind])==len(codes),'Incomplete financial set')
        for q,allowed,unit in zip(financial[kind],codes,units):
            require(q['code'] in allowed and q['unit']==unit and q['date']==t,'Financial identity/unit/date mismatch')
            evidence(q)
            if q['value'] is None:
                require(q.get('status') in ['missing','non_trading'] and q['change'] is None and q.get('missing_reason'),'Unexplained financial missing quote')
                continue
            require(number(q['value']) and q['value']>0 and q.get('basis'),'Invalid financial quote/basis')
            if q['change'] is None:
                require(q.get('change_method')=='missing_previous','Unexplained missing change')
            elif q.get('change_method')=='reported':require(number(q['change']),'Invalid reported change')
            else:
                require(number(q.get('previous_value')) and q['previous_value']>0 and q.get('previous_date','9999')<t,'Invalid financial previous')
                require(math.isclose(q['change'],(q['value']/q['previous_value']-1)*100,abs_tol=1e-9),'Financial change mismatch')
    cutoff=dt.datetime.fromisoformat(news['news_as_of']);require(cutoff.tzinfo is not None,'News cutoff must include timezone')
    require(cutoff.date()<=publication,'News cutoff after publication')
    require(3<=len(news['market_news'])<=5 and len(news['company_news'])>=5,'News counts fail')
    all_news=news['market_news']+news['company_news'];require(len({q['url'] for q in all_news})==len(all_news),'Duplicate article across sections')
    for q in all_news:
        require(q.get('title') and q.get('text'),'Empty article')
        published=dt.date.fromisoformat(q['published'])
        require(publication-dt.timedelta(days=7)<=published<=publication and published<=cutoff.date(),'News outside time window')
        if q.get('published_at'):require(dt.datetime.fromisoformat(q['published_at'])<=cutoff,'News published after cutoff')
        evidence(q)
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--date',required=True);a=p.parse_args()
    validate(a.date);print('PASS: dated inputs, required news count, financial calculations and evidence')
