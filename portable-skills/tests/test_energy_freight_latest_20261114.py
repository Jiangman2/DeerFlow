"""Dated freight references and old-project recovery, with no network/credentials."""
import copy
import datetime as dt
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
SKILL = BASE / 'candidates/energy-market-daily-2.1.1'


def candidate_modules():
    names = ('common', 'market', 'research', 'browser_runtime', 'report_renderer', 'daily')
    prior = {name: sys.modules.pop(name, None) for name in names}
    old_path = sys.path[:]
    try:
        sys.path.insert(0, str(SKILL / 'scripts'))
        modules = {name: importlib.import_module(name) for name in names}
    finally:
        sys.path[:] = old_path
        for name in names:
            sys.modules.pop(name, None)
            if prior[name] is not None:
                sys.modules[name] = prior[name]
    return modules


MODULES = candidate_modules()
common, market, daily, renderer = (MODULES[n] for n in ('common', 'market', 'daily', 'report_renderer'))
TARGET = '2026-11-06'
START = '2026-08-06'


def rows(day, value=100, freight_missing=False, holiday=False):
    result = []
    for code in list(market.SPECS) + market.FREIGHT:
        unit = market.SPECS[code][2] if code in market.SPECS else ('万美元' if market.FREIGHT.index(code) % 2 == 0 else '美元/桶')
        missing = freight_missing and code in market.FREIGHT
        result.append(dict(code=code, date=day, value=None if missing else value,
                           status=('non_trading' if holiday else 'missing') if missing else 'available',
                           unit=unit, item=code, raw_close=('休市' if holiday else '') if missing else str(value)))
    return result


def payload(records):
    return {'errcode': '0', 'data': [dict(code_name=r['code'], value_date=r['date'], unit=r['unit'], item=r['item'], close=r['raw_close']) for r in records]}


def project(root, energy, version='2.1.1-portable'):
    data = json.loads((BASE / 'tests/fixtures/approved.json').read_text(encoding='utf-8-sig'))
    finance = {k: data[k] for k in ('data_date', 'stocks', 'fx')}
    for i, quote in enumerate(finance['stocks'] + finance['fx']):
        file = 'sources/finance-%d.json' % i
        common.save(root / file, {'fixture': True})
        quote.update(evidence=[file], retrieved_at='2026-11-07T07:00:00+08:00')
    articles, news = [], dict(publication_date=data['publication_date'], news_as_of=data['news_as_of'])
    for section in ('market_news', 'company_news'):
        news[section] = []
        for i, item in enumerate(data[section]):
            article_id = section + str(i)
            file = 'sources/' + article_id + '.html'
            raw = item['text'].encode()
            (root / file).write_bytes(raw)
            articles.append(dict(id=article_id, url=item['url'], source=item['source'], published=item['published'],
                                 published_at=item['published'] + 'T00:00:00+08:00', retrieved_at='2026-11-07T07:00:00+08:00',
                                 evidence=[file], evidence_sha256=common.digest(raw), original_title=item['title'], body=item['text']))
            news[section].append(dict(article_id=article_id, title=item['title'], text=item['text']))
    for file, value in (('market.json', energy), ('financial.json', finance), ('research.json', {'articles': articles}), ('news.json', news)):
        common.save(root / file, value)
    common.save(root / 'PROJECT.json', dict(version=version, publication_date=data['publication_date'], data_date=TARGET,
                news_as_of=data['news_as_of'], input_hashes={name: common.digest((root / file).read_bytes())
                for name, file in (('market', 'market.json'), ('financial', 'financial.json'), ('research', 'research.json'))}))


class FreightReferences(unittest.TestCase):
    def test_target_quotes_are_unchanged(self):
        records = rows('2026-11-05', 80) + rows(TARGET, 100)
        actual = market.build(START, TARGET, records, {})
        legacy = market.build(START, TARGET, records, {}, reference_freight=False)
        self.assertEqual(actual['freight'], legacy['freight'])
        self.assertTrue(actual['market_complete'])
        self.assertEqual(actual['freight_reference_count'], 0)

    def test_nearest_quote_and_its_actual_previous_are_selected(self):
        records = rows('2026-11-04', 80) + rows('2026-11-05', 100) + rows(TARGET, freight_missing=True)
        data = market.build(START, TARGET, records, {})
        for q in data['freight']:
            self.assertEqual((q['value'], q['date'], q['previous_date'], q['change']), (100, '2026-11-05', '2026-11-04', 25))
            self.assertEqual((q['target_date'], q['target_status'], q['quote_age_days']), (TARGET, 'missing', 1))
            self.assertEqual(q['status'], 'latest_available')
        self.assertFalse(data['market_complete'])

    def test_eight_series_select_independently(self):
        records = rows('2026-11-04', 80) + rows('2026-11-05', 100) + rows(TARGET, freight_missing=True)
        next(r for r in records if r['date'] == '2026-11-05' and r['code'] == market.FREIGHT[0]).update(value=None, status='missing')
        data = market.build(START, TARGET, records, {})
        self.assertEqual(data['freight'][0]['date'], '2026-11-04')
        self.assertEqual(data['freight'][1]['date'], '2026-11-05')

    def test_future_and_outside_window_not_used(self):
        records = rows('2026-08-05', 80) + rows(TARGET, freight_missing=True) + rows('2026-11-07', 100)
        data = market.build(START, TARGET, records, {})
        self.assertTrue(all(q['value'] is None and q['status'] == 'missing' for q in data['freight']))
        self.assertEqual(data['history']['freight'], [])

    def test_holiday_reference_retains_target_holiday(self):
        data = market.build(START, TARGET, rows('2026-11-05') + rows(TARGET, freight_missing=True, holiday=True), {})
        self.assertTrue(all(q['target_status'] == 'non_trading' for q in data['freight']))
        self.assertIn('目标日休市', market.freight_reference_note(data['freight'][0]))

    def test_technical_failure_not_reported_as_holiday(self):
        data = market.build(START, TARGET, rows('2026-11-05'), {TARGET: 'TRANSPORT_REQUEST_FAILED'})
        self.assertEqual(data['freight'][0]['target_missing_reason'], 'TRANSPORT_REQUEST_FAILED')
        self.assertEqual(data['freight'][0]['target_status'], 'missing')
        self.assertTrue(all(q['value'] is None for k in ('oil', 'gas') for q in data[k]))

    def test_no_unit_crossing_or_history_forward_fill(self):
        records = rows('2026-11-04', 80) + rows('2026-11-05', 100) + rows(TARGET, freight_missing=True)
        for r in records:
            if r['date'] == '2026-11-04':
                r['unit'] = 'old-unit'
        data = market.build(START, TARGET, records, {})
        self.assertTrue(all(q['change'] is None and q['previous_date'] is None for q in data['freight']))
        self.assertTrue(all(TARGET not in line['dates'] for line in data['history']['freight']))

    def test_old_project_reprojects_without_writing_or_refetching(self):
        records = rows('2026-11-04', 80) + rows('2026-11-05', 100) + rows(TARGET, freight_missing=True)
        energy = market.build(START, TARGET, records, {}, reference_freight=False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project(root, energy, version='2.1.0-portable')
            before = {p.name: p.read_bytes() for p in root.glob('*.json')}
            with patch.object(market, 'acquire', side_effect=AssertionError('no recollection')):
                result = daily.snapshot(root)
            self.assertEqual(result['freight'][0]['date'], '2026-11-05')
            self.assertEqual(result['quality'], '数据不完整')
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.json')})
            text = daily.reply_text(result)
            self.assertIn('参考最近已取得报价 2026-11-05', text)
            html = renderer.render(result)
            self.assertIn('参考最近已取得报价 2026-11-05', html)
            self.assertIn('部分日期未完成重新核查', text)

    def test_projection_cannot_forge_value_date_unit_or_change(self):
        original = market.build(START, TARGET, rows('2026-11-04', 80) + rows('2026-11-05', 100) + rows(TARGET, freight_missing=True), {})
        for key, value in (('value', 999), ('date', TARGET), ('unit', '元'), ('change', 0), ('previous_date', '2026-11-05')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                altered = copy.deepcopy(original)
                altered['freight'][0][key] = value
                project(Path(temporary), altered)
                with self.assertRaisesRegex(common.Failure, 'FREIGHT_SOURCE_PROJECTION_MISMATCH'):
                    daily.snapshot(Path(temporary))

    def test_missing_hashed_observations_cannot_authorize_reference(self):
        energy = market.build(START, TARGET, rows('2026-11-05') + rows(TARGET, freight_missing=True), {})
        energy.pop('observations')
        with tempfile.TemporaryDirectory() as temporary:
            project(Path(temporary), energy)
            with self.assertRaisesRegex(common.Failure, 'MARKET_QUOTE_INVALID'):
                daily.snapshot(Path(temporary))

    def test_unchanged_complete_template_is_byte_identical(self):
        spec = importlib.util.spec_from_file_location('original_freight_fixture', BASE / 'tests/fixtures/original_renderer.py')
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        data = json.loads((BASE / 'tests/fixtures/approved.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(original.render(data), renderer.render(data))


class CacheRecheck(unittest.TestCase):
    def test_failed_empty_window_is_not_a_complete_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(market, 'ROOT', root / 'empty'), patch.object(market, 'previous_months', return_value=dt.date(2026, 11, 4)), patch.object(market, 'credentials', side_effect=common.Failure('JKX_API_PASSWORD_REQUIRED')):
                data = market.acquire(root, dt.date(2026, 11, 7), budget=5)
        self.assertEqual(data['freight_reference_count'], 0)
        self.assertTrue(all(q['status'] == 'missing' for q in data['freight']))
        self.assertFalse(data['freight_reference_search_complete'])

    def test_no_reference_checks_whole_window_including_holiday(self):
        for status in ('missing', 'non_trading'):
            with self.subTest(status=status):
                quote = {'status': status, 'date': TARGET}
                self.assertFalse(market.freight_search_complete([quote], START, TARGET, {}, {'2026-08-07': 'TRANSPORT_REQUEST_FAILED'}))
                self.assertTrue(market.freight_search_complete([quote], START, TARGET, {}, {}))

    def test_reference_checks_only_its_relevant_interval(self):
        quote = {'status': 'latest_available', 'date': '2026-11-04'}
        self.assertTrue(market.freight_search_complete([quote], START, TARGET, {'2026-11-03': 'failure'}, {'2026-08-07': 'failure'}))
        self.assertFalse(market.freight_search_complete([quote], START, TARGET, {'2026-11-05': 'failure'}, {}))
        self.assertFalse(market.freight_search_complete([quote], START, TARGET, {}, {'2026-11-04': 'failure'}))

    def test_current_valid_quotes_do_not_depend_on_history_search(self):
        quote = {'status': 'available', 'date': TARGET}
        self.assertTrue(market.freight_search_complete([quote], START, TARGET, {'2026-11-05': 'failure'}, {'2026-08-07': 'failure'}))

    def acquire(self, *, recheck_error=False, no_credentials=False, rejected_credentials=False):
        requested = []
        def get(client, _url, *, params):
            requested.append(params['pdate'])
            client.requests += 1
            day = params['pdate']
            if rejected_credentials:
                raise common.Failure('HTTP_REQUEST_FAILED', http_status=401)
            if day == '2026-11-05' and recheck_error:
                raise common.Failure('TRANSPORT_REQUEST_FAILED')
            return json.dumps(payload(rows(day, 90 if day == '2026-11-05' else 110, freight_missing=day == TARGET))).encode()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / 'cache'
            common.save(cache / 'jkx_new_2026-11-05.json', payload(rows('2026-11-05', 85, freight_missing=True)))
            common.save(cache / 'jkx_new_2026-11-04.json', payload(rows('2026-11-04', 80)))
            credentials = patch.object(market, 'credentials', side_effect=common.Failure('JKX_API_PASSWORD_REQUIRED')) if no_credentials else patch.object(market, 'credentials', return_value=('http://fixture.invalid', 'synthetic'))
            with patch.object(market, 'ROOT', root / 'empty'), patch.object(market, 'previous_months', return_value=dt.date(2026, 11, 4)), credentials, patch.object(market.Client, 'get', get):
                result = market.acquire(root, dt.date(2026, 11, 7), cache=cache, budget=5)
            retained = common.load(cache / 'jkx_new_2026-11-05.json')
        return requested, result, retained

    def test_rechecks_missing_cache_before_older_reference_only_once(self):
        requested, data, _ = self.acquire()
        self.assertEqual(requested, [TARGET, '2026-11-05'])
        self.assertEqual(data['freight'][0]['date'], '2026-11-05')
        self.assertEqual(data['freight'][0]['value'], 90)
        self.assertTrue(data['freight_reference_search_complete'])

    def test_failed_recheck_preserves_cached_oil_and_marks_search_incomplete(self):
        requested, data, retained = self.acquire(recheck_error=True)
        self.assertEqual(requested, [TARGET, '2026-11-05'])
        self.assertEqual(data['freight'][0]['date'], '2026-11-04')
        self.assertEqual(next(r for r in data['observations'] if r['date'] == '2026-11-05' and r['code'] == 'LD0221')['value'], 85)
        self.assertEqual(retained['data'][0]['close'], '85')
        self.assertFalse(data['freight_reference_search_complete'])
        self.assertEqual(data['freight_cache_recheck_failures']['2026-11-05'], 'TRANSPORT_REQUEST_FAILED')

    def test_missing_credentials_never_attempts_provider_request(self):
        requested, data, _ = self.acquire(no_credentials=True)
        self.assertEqual(requested, [])
        self.assertEqual(data['freight'][0]['date'], '2026-11-04')
        self.assertFalse(data['freight_reference_search_complete'])

    def test_rejected_credentials_are_not_replayed_for_older_dates(self):
        requested, data, _ = self.acquire(rejected_credentials=True)
        self.assertEqual(requested, [TARGET])
        self.assertEqual(data['freight'][0]['date'], '2026-11-04')
        self.assertFalse(data['freight_reference_search_complete'])


if __name__ == '__main__':
    unittest.main()
