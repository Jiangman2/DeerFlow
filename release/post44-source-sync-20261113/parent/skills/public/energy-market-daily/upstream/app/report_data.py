"""Read source workbooks without modifying them; retain per-series dates and provenance."""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from openpyxl import load_workbook


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quote(name, unit, points, target, source, **extra):
    records = sorted((day, value) for day, value in points.items() if day <= target)
    result = dict(name=name, unit=unit, value=None, change=None, source=source, **extra)
    if not records:
        return result
    day, value = records[-1]
    result.update(value=value, date=day.isoformat())
    if len(records) > 1:
        previous_day, previous = records[-2]
        result.update(previous_value=previous, previous_date=previous_day.isoformat(),
                      change=(value/previous-1)*100 if previous else None)
    return result


def load_report(publication, start, folder, news_path):
    target = publication - dt.timedelta(days=1)
    energy_path = folder / f'energy_{target.isoformat()}.json'
    def history(name, points, axis='left'):
        records = sorted((day,value) for day,value in points.items() if start<=day<=target)
        return dict(name=name,axis=axis,dates=[d.isoformat() for d,v in records],values=[v for d,v in records])
    if not energy_path.exists():
        if publication >= dt.date(2026,11,6):
            raise ValueError('Prepare the dated energy API snapshot; stale workbook substitution is disabled')
        oil_path = folder / 'oil.xlsx'
        gas_paths = list(folder.glob('LNG*.xlsx'))
        if len(gas_paths) != 1:
            raise ValueError('Expected exactly one LNG workbook')
        gas_path = gas_paths[0]
        oil_map = [('brent','布伦特原油期货','美元/桶'),('dtdbrent','布伦特原油现货','美元/桶'),
                   ('wti','WTI 原油期货','美元/桶')]
        oil_points = {key:{} for key,_,_ in oil_map}
        workbook = load_workbook(oil_path, read_only=True, data_only=True)
        sheet = workbook['oil']
        sheet.reset_dimensions()
        rows = sheet.iter_rows(max_col=7, values_only=True)
        headers = list(next(rows))
        for row in rows:
            if not isinstance(row[0], dt.datetime):
                continue
            day = row[0].date()
            for key,_,_ in oil_map:
                value = row[headers.index(key)]
                if numeric(value):
                    if day in oil_points[key] and oil_points[key][day] != value:
                        raise ValueError(f'Conflicting {key} values on {day}')
                    oil_points[key][day] = float(value)
        workbook.close()
        gas_points = {name:{} for name in ['Henry Hub','LNG East Asia','TTF']}
        workbook = load_workbook(gas_path, read_only=True, data_only=True)
        # Power BI exports may declare A1:A1 despite containing thousands of rows.
        workbook.active.reset_dimensions()
        for row in workbook.active.iter_rows(min_row=4, values_only=True):
            if isinstance(row[0], dt.datetime) and row[1] in gas_points and numeric(row[3]):
                day, name, value = row[0].date(), row[1], float(row[3])
                if day in gas_points[name] and gas_points[name][day] != value:
                    raise ValueError(f'Conflicting {name} values on {day}')
                gas_points[name][day] = value
        workbook.close()
        oil = [quote(name,unit,oil_points[key],target,oil_path.name,field=key) for key,name,unit in oil_map]
        gas_names = {'LNG East Asia':'东北亚 LNG 现货（East Asia spot）'}
        gas = [quote(gas_names.get(name,name),'美元/百万英热单位',points,target,gas_path.name,
                     field='WeeklyCummulativeAvgPrice',basis='当周累计均价',unit_basis='沿用用户原模板，原数据文件未列单位') for name,points in gas_points.items()]
        if any(item['value'] is None for item in oil + gas):
            raise ValueError('A required oil/gas series has no valid observations before the data cutoff.')
        histories = dict(start=start.isoformat(),end=target.isoformat(),
                         oil=[history(name,oil_points[key],'right' if key=='shanghai_crude' else 'left') for key,name,unit in oil_map],
                         gas=[history(gas_names.get(name,name),points,'right' if name=='Henry Hub' else 'left') for name,points in gas_points.items()])
    energy_path = folder / f'energy_{target.isoformat()}.json'
    if energy_path.exists():
        energy = json.loads(energy_path.read_text(encoding='utf-8'))
        if energy['data_date'] != target.isoformat():
            raise ValueError('Energy snapshot date mismatch')
        oil, gas = energy['oil'], energy['gas']
        for item in oil + gas:
            if item['date'] != target.isoformat() or not numeric(item['value']):
                raise ValueError('Invalid energy quote')
        # Legacy spreadsheets have unconfirmed contract/averaging definitions.
        # Do not splice them into the newly supplied API series.
        histories = dict(start=start.isoformat(), end=target.isoformat(), single_day=True)
        for kind, items in [('oil', oil), ('gas', gas)]:
            histories[kind] = [history(x['name'], {target:x['value']},
                'right' if x['name']=='Henry Hub' else 'left') for x in items]
        if energy.get('history'):
            histories = energy['history']
            if histories['start'] != start.isoformat() or histories['end'] != target.isoformat():
                raise ValueError('API history window mismatch')
    elif publication >= dt.date(2026,11,6):
        raise ValueError('Prepare the dated energy API snapshot; stale workbook substitution is disabled')
    stocks = [dict(name=name,code=code,unit=unit,value=None,change=None) for name,code,unit in [
        ('中海油','600938.SH','元'),('中石油','601857.SH','元'),('中石化','600028.SH','元'),('沪A指数','000002.SH','点')]]
    fx = [dict(name='美元指数',unit='点',value=None,change=None),dict(name='美元兑人民币',unit='元/美元',value=None,change=None,decimals=4)]
    financial_path = folder / f'financial_{target.isoformat()}.json'
    if financial_path.exists():
        financial = json.loads(financial_path.read_text(encoding='utf-8'))
        if financial['data_date'] != target.isoformat():
            raise ValueError('Financial snapshot date mismatch')
        for item in financial['stocks'] + financial['fx']:
            if item['date'] != target.isoformat():
                raise ValueError('Financial snapshot date mismatch')
            if item['value'] is None and item.get('status') in ('missing','non_trading') and item.get('missing_reason'):
                continue
            if not numeric(item['value']):
                raise ValueError('Financial snapshot contains invalid value')
            if item['change'] is None and item.get('change_method') == 'missing_previous':
                continue
            if item.get('change_method') != 'reported':
                expected = (item['value']/item['previous_value']-1)*100
                if not math.isclose(expected, item['change'], abs_tol=1e-9):
                    raise ValueError('Financial percentage change does not match closes')
        stocks, fx = financial['stocks'], financial['fx']
    stocks[0] = dict(stocks[0], name='海油 A 股')
    if not any(x['code'] in ('00883.HK','hk00883') for x in stocks):
        stocks.insert(1, dict(name='海油港股',code='00883.HK',unit='港元',value=None,change=None))
    freight = [dict(name=name,unit='航线及单位待定',value=None,change=None)
               for name in ['原油运输','LNG 运输']]
    if energy_path.exists() and energy.get('freight'):
        freight = energy['freight']
    news = json.loads(news_path.read_text(encoding='utf-8'))
    if news['publication_date'] != publication.isoformat():
        raise ValueError('News file publication date must match --date; prepare a fresh news file.')
    for section in ['market_news','company_news']:
        if section == 'company_news' and publication >= dt.date(2026,11,6):
            if len(news[section]) < 5:
                raise ValueError('Company news requires at least five verified, distinct stories')
            if len({item['url'] for item in news[section]}) != len(news[section]):
                raise ValueError('Company news must not split one source article into multiple stories')
        elif not 0<=len(news[section])<=5:
            raise ValueError('Market news supports at most five verified stories')
        if len(news[section])<3 and not news.get('shortfall_reason',{}).get(section):
            raise ValueError('Fewer than three stories requires a documented sourcing shortfall')
        for item in news[section]:
            if dt.date.fromisoformat(item['published']) > publication or not item['url'].startswith('https://'):
                raise ValueError('Invalid news date or source URL')
    source_paths = [energy_path,news_path] if energy_path.exists() else [oil_path,gas_path,news_path]
    sources = [dict(file=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in source_paths]
    if financial_path.exists():
        sources.append(dict(file=financial_path.name,sha256=hashlib.sha256(financial_path.read_bytes()).hexdigest()))
    return dict(publication_date=publication.isoformat(),data_date=target.isoformat(),
                oil=oil,gas=gas,freight=freight,stocks=stocks,fx=fx,history=histories,sources=sources,
                market_news=news['market_news'],company_news=news['company_news'],news_as_of=news['news_as_of'],
                gas_source='金凯讯' if energy_path.exists() else 'Rystad Energy',
                api_energy=energy_path.exists())
