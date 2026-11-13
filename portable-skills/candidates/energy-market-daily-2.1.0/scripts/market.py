"""JKX dated history and exact original units/calculations, with explicit gaps."""
from __future__ import annotations

import calendar
import datetime as dt
import json
import math
from pathlib import Path
import shutil
import time

from common import Client, Failure, ROOT, credentials, load, now, save

SPECS = {
    'LD0221': ('oil', '布伦特原油期货', '美元/桶'), 'XA0008': ('oil', '布伦特原油现货', '美元/桶'),
    'NY0121': ('oil', 'WTI 原油期货', '美元/桶'), 'NY0421': ('gas', 'Henry Hub', '美元/百万BTU'),
    'XA0942': ('gas', 'JKM', '美元/百万BTU'), 'LD1221': ('gas', 'TTF', '美元/百万BTU'),
}
FREIGHT = ['LPGD59', 'LPGD60', 'LPG1225', 'LPG1224', 'LPG1179', 'LPG1177', 'LPG1227', 'LPG1226']
NAMES = ['西非-中国（整船）', '西非-中国（美元/桶）', '阿曼湾-中国（整船）', '阿曼湾-中国（美元/桶）',
         '美湾-中国（整船）', '美湾-中国（美元/桶）', '波斯湾-中国（整船）', '波斯湾-中国（美元/桶）']
HOLIDAYS = {'周六', '周日', '休市', '节假日', '假日'}
MISSING = {'', '-', '--', '暂无', '暂无数据', '未更新'}


def previous_months(day, count=3):
    year, month = divmod(day.year * 12 + day.month - 1 - count, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def normalize(payload, day):
    if not isinstance(payload, dict) or str(payload.get('errcode')) != '0' or not isinstance(payload.get('data'), list):
        raise Failure('PROVIDER_RESPONSE_INVALID')
    rows, seen = [], set()
    for item in payload['data']:
        try:
            code = item['code_name']
            if code in seen:
                raise Failure('DUPLICATE_PROVIDER_CODE')
            seen.add(code)
            date = dt.datetime.strptime(item['value_date'].replace('/', '-'), '%Y-%m-%d').date().isoformat()
            if date != day:
                raise Failure('PROVIDER_DATE_MISMATCH')
            raw = str(item['close']).strip()
            try:
                value = float(raw)
                if not math.isfinite(value) or value <= 0:
                    raise Failure('PROVIDER_NUMBER_INVALID')
                status = 'available'
            except ValueError:
                value = None
                if raw in HOLIDAYS:
                    status = 'non_trading'
                elif raw in MISSING:
                    status = 'missing'
                else:
                    raise Failure('PROVIDER_UNKNOWN_STATUS') from None
            if code in SPECS and item['unit'] != SPECS[code][2]:
                raise Failure('PROVIDER_UNIT_MISMATCH')
            if code in set(SPECS) | set(FREIGHT):
                rows.append(dict(code=code, date=date, value=value, status=status, unit=str(item['unit']), item=str(item['item']), raw_close=raw))
        except (KeyError, TypeError, AttributeError, ValueError):
            raise Failure('PROVIDER_ROW_INVALID') from None
    if not set(SPECS).union(FREIGHT) <= seen:
        raise Failure('PROVIDER_SERIES_INCOMPLETE')
    return rows


def build(start, end, records, failures):
    snapshot = dict(data_date=end, start=start, end=end, retrieved_at=now().isoformat(), oil=[], gas=[], freight=[],
                    history=dict(start=start, end=end, single_day=False, oil=[], gas=[], freight=[]), observations=records,
                    acquisition_failures=failures)
    for code in list(SPECS) + FREIGHT:
        rows = sorted((r for r in records if r['code'] == code), key=lambda r: r['date'])
        current = next((r for r in rows if r['date'] == end), None)
        kind, name, default_unit = SPECS[code] if code in SPECS else ('freight', NAMES[FREIGHT.index(code)], '万美元' if FREIGHT.index(code) % 2 == 0 else '美元/桶')
        unit = current['unit'] if current else default_unit
        previous = next((r for r in reversed(rows) if r['date'] < end and r['value'] is not None), None)
        same = previous and previous['unit'] == unit
        value = current['value'] if current else None
        status = current['status'] if current else 'missing'
        quote = dict(name=name, code=code, unit=unit, date=end, value=value, status=status,
                     change=(value / previous['value'] - 1) * 100 if value is not None and same else None,
                     previous_date=previous['date'] if same else None, previous_value=previous['value'] if same else None,
                     source='金凯讯', contract=current['item'] if current else '', basis='接口 close；名称及单位沿用接口',
                     decimals=3 if kind == 'gas' else 2, change_method='calculated' if value is not None and same else 'unit_changed_or_missing_previous')
        if value is None:
            quote['missing_reason'] = '供应商明确休市' if status == 'non_trading' else failures.get(end, '未取得目标日有效报价')
        snapshot[kind].append(quote)
        segments, segment, interrupted = [], None, False
        row_map = {r['date']: r for r in rows}
        date = dt.date.fromisoformat(start)
        while date.isoformat() <= end:
            row = row_map.get(date.isoformat())
            date += dt.timedelta(days=1)
            if row is None or row['status'] == 'missing':
                interrupted = True
                continue
            if row['value'] is None:
                continue  # authentic non-trading days are not filled in
            if segment is None or segment['unit'] != row['unit']:
                segment = dict(name=name, code=code, unit=row['unit'], axis='right' if code == 'NY0421' else 'left', dates=[], values=[], break_before=[])
                segments.append(segment)
            if interrupted and segment['dates']:
                segment['break_before'].append(len(segment['dates']))
            interrupted = False
            segment['dates'].append(row['date'])
            segment['values'].append(row['value'])
        snapshot['history'][kind].extend(segments)
    snapshot['market_complete'] = not failures and all(q['status'] in ('available', 'non_trading') for k in ('oil', 'gas', 'freight') for q in snapshot[k])
    return snapshot


def acquire(project, publication, credential_path=None, cache=None, budget=120):
    target = publication - dt.timedelta(days=1)
    start = previous_months(target)
    cache = Path(cache) if cache else Path(project) / 'cache' / 'jkx'
    cache.mkdir(parents=True, exist_ok=True)
    records, failures = [], {}
    client = Client(time.monotonic() + budget)
    try:
        endpoint, password = credentials(credential_path)
    except Failure as exc:
        endpoint, password = None, None
        failures[target.isoformat()] = exc.code
    # Fetch target first, always fresh. Historical cache is used only for history.
    days = [target - dt.timedelta(days=i) for i in range((target - start).days + 1)]
    for day in days:
        date = day.isoformat()
        path = cache / ('jkx_new_' + date + '.json')
        seeded = ROOT / 'assets' / 'history' / path.name
        if day != target and not path.exists() and seeded.is_file():
            shutil.copyfile(seeded, path)
        try:
            if day != target and path.is_file():
                rows = normalize(load(path), date)
            else:
                if not endpoint:
                    raise Failure('JKX_CREDENTIAL_UNAVAILABLE')
                raw = client.get(endpoint, params={'p': password, 'pdate': date})
                payload = json.loads(raw)
                rows = normalize(payload, date)
                # Store only the contracted data fields, never provider error bodies.
                clean = {'errcode': '0', 'data': [{key: item[key] for key in ('code_name', 'item', 'value_date', 'unit', 'close')} for item in payload['data']]}
                save(path, clean)
            records.extend(rows)
        except (Failure, ValueError, KeyError, OSError) as exc:
            failures.setdefault(date, exc.code if isinstance(exc, Failure) else 'PROVIDER_RESPONSE_INVALID')
    snapshot = build(start.isoformat(), target.isoformat(), records, failures)
    snapshot['provider_requests'] = client.requests
    save(Path(project) / 'market.json', snapshot)
    return snapshot
