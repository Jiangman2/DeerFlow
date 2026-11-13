"""Portable observable-behavior tests; no external network or real credential."""
# ruff: noqa: E402 -- test the standalone scripts without installing a package.
import argparse
import copy
import datetime as dt
import importlib.util
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error

SKILL = Path(__file__).resolve().parents[1] / 'energy-market-daily'
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
sys.path.insert(0, str(SKILL / 'scripts'))
import common
import daily
import market
import research
import report_renderer


def project_fixture(path):
    data = json.loads((FIXTURES / 'approved.json').read_text(encoding='utf-8-sig'))
    energy = {key: data[key] for key in ('data_date', 'oil', 'gas', 'freight', 'history')}
    energy.update(market_complete=True, acquisition_failures={})
    finance = {key: data[key] for key in ('data_date', 'stocks', 'fx')}
    for index, quote in enumerate(finance['stocks'] + finance['fx']):
        file = 'sources/finance-%d.txt' % index
        common.save(path / file, {'source': 'fixture', 'date': data['data_date']})
        quote.update(evidence=[file], retrieved_at='2026-11-09T10:00:00+08:00')
    articles, summaries = [], {'market_news': [], 'company_news': []}
    for section in summaries:
        for index, quote in enumerate(data[section]):
            key = section + str(index)
            file = 'sources/' + key + '.html'
            raw = ('<html><p>' + quote['text'] + '</p></html>').encode()
            (path / file).write_bytes(raw)
            articles.append(dict(id=key, url=quote['url'], source=quote['source'], published=quote['published'],
                                 published_at=quote['published'] + 'T00:00:00+08:00', retrieved_at='2026-11-09T10:00:00+08:00',
                                 evidence=[file], evidence_sha256=common.digest(raw), original_title=quote['title'], body=quote['text'], suggested_section=section))
            summaries[section].append(dict(article_id=key, title=quote['title'], text=quote['text']))
    news = dict(publication_date=data['publication_date'], news_as_of=data['news_as_of'], **summaries)
    research_data = dict(publication_date=data['publication_date'], news_as_of=data['news_as_of'], articles=articles)
    for file, value in (('market.json', energy), ('financial.json', finance), ('research.json', research_data), ('news.json', news)):
        common.save(path / file, value)
    state = dict(version=daily.VERSION, publication_date=data['publication_date'], data_date=data['data_date'], news_as_of=data['news_as_of'],
                 input_hashes={key: common.digest((path / file).read_bytes()) for key, file in (('market', 'market.json'), ('financial', 'financial.json'), ('research', 'research.json'))})
    common.save(path / 'PROJECT.json', state)
    return data


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ep-')
        self.path = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_original_complete_template_is_unchanged(self):
        spec = importlib.util.spec_from_file_location('fixture_original', FIXTURES / 'original_renderer.py')
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        data = json.loads((FIXTURES / 'approved.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(original.render(data), report_renderer.render(data))

    def test_complete_snapshot_matches_original_amounts(self):
        expected = project_fixture(self.path)
        actual = daily.snapshot(self.path)
        for kind in ('oil', 'gas', 'freight', 'stocks', 'fx'):
            self.assertEqual([q['value'] for q in expected[kind]], [q['value'] for q in actual[kind]])
        self.assertTrue(actual['formal_report_ready'])

    def test_browser_fix_reuses_prepared_previous_version_without_collection(self):
        expected = project_fixture(self.path)
        state = common.load(self.path / 'PROJECT.json')
        state['version'] = '2.0.0-portable'
        common.save(self.path / 'PROJECT.json', state)
        before = {name: (self.path / name).read_bytes() for name in ('market.json', 'financial.json', 'research.json', 'news.json')}
        with patch.object(daily, 'acquisition_job', side_effect=AssertionError('must reuse')):
            result = daily.begin(argparse.Namespace(workspace=str(self.path), date=None, news_as_of=None))
        self.assertFalse(result['collection_restarted'])
        self.assertEqual(daily.snapshot(self.path)['oil'], expected['oil'])
        self.assertEqual(before, {name: (self.path / name).read_bytes() for name in before})

    def test_reply_news_links_use_only_reconciled_original_urls(self):
        project_fixture(self.path)
        data = daily.snapshot(self.path)
        text = daily.reply_text(data)
        for section in ('market_news', 'company_news'):
            for article in data[section]:
                self.assertIn('](' + article['url'].replace('(', '%28').replace(')', '%29') + ')', text)
                self.assertIn('来源：' + article['source'] + ' ' + article['published'], text)
        self.assertNotIn('/drive/home', text)
        self.assertNotIn('workspace/chats', text)  # current task URL belongs to host

    def test_raw_source_change_is_rejected(self):
        project_fixture(self.path)
        (self.path / 'market.json').write_text('{}')
        with self.assertRaisesRegex(common.Failure, 'PREPARED_SOURCE_CHANGED'):
            daily.snapshot(self.path)

    def test_article_evidence_change_is_rejected(self):
        project_fixture(self.path)
        (self.path / 'sources/market_news0.html').write_text('changed')
        with self.assertRaisesRegex(common.Failure, 'EVIDENCE_CHANGED'):
            daily.snapshot(self.path)

    def test_news_cannot_override_source_url_or_date(self):
        data = project_fixture(self.path)
        news = common.load(self.path / 'news.json')
        news['market_news'][0].update(url='https://wrong.invalid/other', published='2099-01-01')
        common.save(self.path / 'news.json', news)
        actual = daily.snapshot(self.path)
        self.assertEqual(data['market_news'][0]['url'], actual['market_news'][0]['url'])
        self.assertEqual(data['market_news'][0]['published'], actual['market_news'][0]['published'])

    def test_duplicate_news_rejected(self):
        project_fixture(self.path)
        news = common.load(self.path / 'news.json')
        news['company_news'][0]['article_id'] = news['market_news'][0]['article_id']
        common.save(self.path / 'news.json', news)
        with self.assertRaisesRegex(common.Failure, 'NEWS_SOURCE_OR_DUPLICATE_INVALID'):
            daily.snapshot(self.path)

    def test_news_minimum_is_not_silently_relaxed(self):
        project_fixture(self.path)
        news = common.load(self.path / 'news.json')
        news['company_news'] = news['company_news'][:4]
        common.save(self.path / 'news.json', news)
        with self.assertRaisesRegex(common.Failure, 'NEWS_COUNT_INSUFFICIENT'):
            daily.snapshot(self.path)

    def test_finance_previous_close_calculation(self):
        proof = {'retrieved_at': '2026-11-11T07:00:00+08:00', 'file': 'sources/test.txt'}
        quote = research.financial_quote('海油', '600938.SH', '元', '2026-11-10', [('2026-11-09', 10), ('2026-11-10', 11), ('2026-11-11', 99)], 'fixture', 'https://example.com/history', proof)
        self.assertEqual(quote['value'], 11)
        self.assertAlmostEqual(quote['change'], 10)
        self.assertEqual(quote['previous_date'], '2026-11-09')

    def test_no_current_finance_never_carries_old_close(self):
        proof = {'retrieved_at': '2026-11-11T07:00:00+08:00', 'file': 'sources/test.txt'}
        quote = research.financial_quote('海油', '600938.SH', '元', '2026-11-10', [('2026-11-09', 10)], 'fixture', 'https://example.com/history', proof)
        self.assertIsNone(quote['value'])
        self.assertEqual(quote['status'], 'missing')

    def test_fx_original_raw_source_close_columns(self):
        for file, expected in (('dxy-source.txt', 102.2864), ('cny-source.txt', 6.6763)):
            data = research.jsonp((FIXTURES / file).read_bytes())
            self.assertIsInstance(data, str)
            row = next(row.split(',') for row in data.split('|') if row.startswith('2026-11-06,'))
            self.assertAlmostEqual(float(row[4]), expected)

    def test_jsonp_never_executes_javascript(self):
        for payload in (b'alert(1);', b'var x=(__import__("os").system("false"));', b'callback({"x":1});evil()'):
            with self.subTest(payload=payload):
                with self.assertRaises((ValueError, common.Failure)):
                    research.jsonp(payload)

    def test_credentials_support_author_key_and_url_alias(self):
        file = self.path / 'private.json'
        common.save(file, {'JKX_API_ENDPOINT': 'http://provider.example/path', 'JKX_API_PASSWORD': 'fixture-private'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(common.credentials(str(file)), ('http://provider.example/path', 'fixture-private'))
        common.save(file, {'JKX_API_URL': 'http://provider.example/path?p=fixture-private&pdate=old'})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(common.credentials(str(file)), ('http://provider.example/path', 'fixture-private'))

    def test_environment_wins_over_private_file(self):
        file = self.path / 'private.json'
        common.save(file, {'JKX_API_ENDPOINT': 'http://provider.example/path', 'JKX_API_PASSWORD': 'file-private'})
        with patch.dict(os.environ, {'JKX_API_URL': 'https://preferred.example/path', 'JKX_API_PASSWORD': 'env-private'}):
            self.assertEqual(common.credentials(str(file)), ('https://preferred.example/path', 'env-private'))

    def test_transport_error_does_not_export_password(self):
        client = common.Client(time.monotonic() + 5)
        with patch.object(client.opener, 'open', side_effect=urllib.error.URLError('http://example.com?p=fixture-private')):
            with self.assertRaises(common.Failure) as captured:
                client.get('http://example.com', params={'p': 'fixture-private'})
        self.assertNotIn('fixture-private', str(captured.exception))
        self.assertNotIn('fixture-private', json.dumps(captured.exception.details))

    def test_provider_redirect_never_replays_credential(self):
        client = common.Client(time.monotonic() + 5)
        error = urllib.error.HTTPError('http://example.com', 302, 'redirect', {'Location': 'https://other.example/'}, None)
        with patch.object(client.opener, 'open', side_effect=error) as mocked:
            with self.assertRaises(common.Failure):
                client.get('http://example.com', params={'p': 'fixture-private'})
        self.assertEqual(mocked.call_count, 1)

    def test_public_news_cannot_redirect_to_unlisted_host(self):
        client = common.Client(time.monotonic() + 5)
        with self.assertRaisesRegex(common.Failure, 'SOURCE_HOST_REJECTED'):
            client.get('https://internal.example/private', allowed_hosts=research.NEWS_HOSTS)

    def test_empty_history_and_missing_freight_render(self):
        data = project_fixture(self.path)
        data['formal_report_ready'] = False
        for kind in ('oil', 'gas', 'freight'):
            data['history'][kind] = []
            for quote in data[kind]:
                quote.update(value=None, change=None, status='missing')
        html = report_renderer.render(data)
        self.assertIn('数据不完整', html)
        self.assertIn('数据未取得', html)
        self.assertIn('未取得可核实的历史曲线', html)

    def test_explicit_supplier_holiday_not_technical_failure(self):
        payload = common.load(next((SKILL / 'assets/history').glob('jkx_new_2026-11-06.json')))
        for row in payload['data']:
            row['close'] = '休市'
        records = market.normalize(payload, '2026-11-06')
        data = market.build('2026-08-06', '2026-11-06', records, {})
        self.assertTrue(data['market_complete'])
        self.assertTrue(all(q['status'] == 'non_trading' and q['value'] is None for k in ('oil', 'gas', 'freight') for q in data[k]))

    def test_unrecognized_or_wrong_date_provider_response_rejected(self):
        original = common.load(SKILL / 'assets/history/jkx_new_2026-11-06.json')
        for mutation in ('date', 'duplicate', 'number', 'status', 'unit'):
            with self.subTest(mutation=mutation):
                payload = copy.deepcopy(original)
                if mutation == 'date':
                    payload['data'][0]['value_date'] = '2026-11-05'
                elif mutation == 'duplicate':
                    payload['data'].append(payload['data'][0])
                elif mutation == 'number':
                    payload['data'][0]['close'] = 'NaN'
                elif mutation == 'status':
                    payload['data'][0]['close'] = 'source failed but maybe holiday'
                else:
                    payload['data'][0]['unit'] = 'wrong-unit'
                with self.assertRaises(common.Failure):
                    market.normalize(payload, '2026-11-06')

    def test_unit_change_does_not_calculate_change(self):
        records = [{'code': code, 'date': day, 'value': value, 'status': 'available', 'unit': unit, 'item': code, 'raw_close': str(value)}
                   for code in list(market.SPECS) + market.FREIGHT for day, value, unit in [('2026-11-05', 5, 'old'), ('2026-11-06', 10, 'new')]]
        data = market.build('2026-08-06', '2026-11-06', records, {})
        for kind in ('oil', 'gas', 'freight'):
            self.assertTrue(all(q['change'] is None for q in data[kind]))
            self.assertTrue(all(len(s['dates']) == 1 for s in data['history'][kind]))

    def test_target_day_always_fresh_even_with_cache(self):
        cached = common.load(SKILL / 'assets/history/jkx_new_2026-11-06.json')
        cache = self.path / 'cache'
        cache.mkdir()
        common.save(cache / 'jkx_new_2026-11-06.json', cached)
        requested = []

        class FakeClient:
            requests = 0
            def __init__(self, *args, **kwargs):
                pass
            def get(self, url, *, params):
                self.requests += 1
                requested.append(params['pdate'])
                result = copy.deepcopy(cached)
                next(row for row in result['data'] if row['code_name'] == 'LD0221')['close'] = '123.45'
                return json.dumps(result).encode()

        with patch.object(market, 'Client', FakeClient), patch.object(market, 'credentials', return_value=('http://example.com', 'fixture-private')):
            data = market.acquire(self.path, dt.date(2026, 11, 7), cache=cache, budget=5)
        self.assertEqual(requested, ['2026-11-06'])
        self.assertAlmostEqual(data['oil'][0]['value'], 123.45)

    def test_technical_target_failure_never_uses_cached_current(self):
        cache = self.path / 'cache'
        cache.mkdir()
        common.save(cache / 'jkx_new_2026-11-06.json', common.load(SKILL / 'assets/history/jkx_new_2026-11-06.json'))
        with patch.object(market, 'credentials', side_effect=common.Failure('JKX_API_PASSWORD_REQUIRED')):
            data = market.acquire(self.path, dt.date(2026, 11, 7), cache=cache, budget=5)
        self.assertIsNone(data['oil'][0]['value'])
        self.assertFalse(data['market_complete'])
        self.assertEqual(data['oil'][0]['status'], 'missing')

    def test_worker_timeout_is_bounded_and_marks_missing(self):
        args = argparse.Namespace(market_budget=5, finance_budget=5, news_budget=5, credentials=None, cache=None)
        with patch.object(daily.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 20)) as mocked:
            data = daily.acquisition_job('market', self.path, dt.date(2026, 11, 7), common.now(), args)
        self.assertEqual(mocked.call_args.kwargs['timeout'], 20)
        self.assertFalse(data['market_complete'])
        self.assertTrue(all(q['value'] is None for q in data['oil']))

    def test_prepared_project_does_not_repeat_collection(self):
        project_fixture(self.path)
        args = argparse.Namespace(workspace=str(self.path), date=None, news_as_of=None)
        with patch.object(daily, 'acquisition_job', side_effect=AssertionError('must not refetch')):
            result = daily.begin(args)
        self.assertEqual(result['status'], 'SOURCES_ALREADY_PREPARED')
        self.assertFalse(result['collection_restarted'])

    def test_commands_are_self_contained(self):
        result = daily.command('build', self.path)
        self.assertNotIn('$SKILL_DIR', result)
        self.assertNotIn('$PROJECT_ROOT', result)
        self.assertIn(str(self.path), result)
        self.assertIn(str(SKILL / 'scripts/daily.py'), result)

    def test_png_reuse_does_not_launch_browser(self):
        project_fixture(self.path)
        with patch.object(daily, 'png', return_value={'width': 780, 'height': 1200}):
            (self.path / 'outputs').mkdir()
            (self.path / 'outputs/油气与金融市场日报_2026-11-07.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00'*8 + struct.pack('>II',780,1200) + b'\x00'*1024)
            daily.build(argparse.Namespace(workspace=str(self.path)))
        with patch.object(daily, 'png', side_effect=AssertionError('must reuse')):
            result = daily.build(argparse.Namespace(workspace=str(self.path)))
        self.assertTrue(result['reused'])

    def test_accept_requires_real_visual_flag(self):
        with self.assertRaisesRegex(common.Failure, 'ACTUAL_VISUAL_REVIEW_REQUIRED'):
            daily.accept(argparse.Namespace(workspace=str(self.path), visual_reviewed=False))

    def test_feed_never_selects_future_news(self):
        raw = b'<rss><channel><item><title>Oil price report</title><link>https://oilprice.com/article</link><pubDate>Wed, 11 Nov 2026 10:00:00 +0000</pubDate></item></channel></rss>'
        result = research.feed_items(raw, 'fixture', dt.datetime(2026, 11, 11, 7, tzinfo=common.BEIJING))
        self.assertEqual(result, [])

    def test_supplement_imports_real_page_and_updates_only_research_hash(self):
        project_fixture(self.path)
        before = common.load(self.path / 'PROJECT.json')
        file = self.path / 'sources/extra.html'
        file.write_text('<html><title>Verified company project announcement</title><meta property="article:published_time" content="2026-11-06T12:00:00+08:00"><p>A verified project announcement with concrete factual detail and a genuine archived original page for this fixture.</p></html>', encoding='utf-8')
        common.save(self.path / 'add.json', [{'url': 'https://example.com/project', 'source': 'fixture company', 'published_at': '2026-11-06T12:00:00+08:00', 'evidence': 'sources/extra.html'}])
        result = daily.import_news(argparse.Namespace(workspace=str(self.path), sources=str(self.path / 'add.json')))
        after = common.load(self.path / 'PROJECT.json')
        self.assertEqual(result['added'], 1)
        self.assertEqual(before['input_hashes']['market'], after['input_hashes']['market'])
        self.assertEqual(before['input_hashes']['financial'], after['input_hashes']['financial'])
        self.assertNotEqual(before['input_hashes']['research'], after['input_hashes']['research'])
        self.assertTrue(daily.snapshot(self.path)['formal_report_ready'])

    def test_news_selection_does_not_fetch_or_change_evidence(self):
        project_fixture(self.path)
        catalog = common.load(self.path / 'research.json')
        market_ids = [a['id'] for a in catalog['articles'] if a['suggested_section'] == 'market_news']
        company_ids = [a['id'] for a in catalog['articles'] if a['suggested_section'] == 'company_news']
        before = (self.path / 'research.json').read_bytes()
        with patch.object(research.Client, 'get', side_effect=AssertionError('must not fetch')):
            result = daily.select_news(argparse.Namespace(workspace=str(self.path), market_ids=','.join(market_ids), company_ids=','.join(company_ids)))
        self.assertEqual(result['market_count'], 3)
        self.assertEqual((self.path / 'research.json').read_bytes(), before)

    def test_missing_output_cannot_be_visually_accepted(self):
        project_fixture(self.path)
        common.save(self.path / 'RESULT.json', {'version': daily.VERSION, 'status': 'REPORT_ACCEPTED', 'project': str(self.path), 'files': []})
        with self.assertRaisesRegex(common.Failure, 'RESULT_FILE_SET_INVALID'):
            daily.accept(argparse.Namespace(workspace=str(self.path), visual_reviewed=True))

    def test_real_worker_runs_from_fresh_directory_without_shell_state(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith('JKX_') and key != 'ENERGY_DAILY_CREDENTIALS'}
        result = subprocess.run([sys.executable, '-I', '-B', str(SKILL / 'scripts/daily.py'), '_acquire', '--task', 'market',
                                 '--workspace', str(self.path), '--date', '2026-11-07', '--market-budget', '5'],
                                cwd=self.path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertEqual(json.loads(result.stdout)['status'], 'ACQUISITION_FINISHED')
        self.assertFalse(common.load(self.path / 'market.json')['market_complete'])

    def test_finance_proof_hash_prevents_evidence_replacement(self):
        file = self.path / 'source.txt'
        file.write_text('original', encoding='utf-8')
        quote = {'evidence': ['source.txt'], 'evidence_sha256': common.digest(file.read_bytes()), 'retrieved_at': '2026-11-11T07:00:00+08:00'}
        daily.evidence_valid(self.path, quote)
        file.write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(common.Failure, 'EVIDENCE_CHANGED'):
            daily.evidence_valid(self.path, quote)

    def test_news_acquisition_stops_after_small_sufficient_batch(self):
        cutoff = dt.datetime(2026, 11, 11, 7, tzinfo=common.BEIJING)
        items = []
        for index in range(20):
            title = 'Oil market inventory news %d' % index if index < 5 else 'Oil drilling company contract %d' % index
            items.append('<item><title>%s</title><link>https://oilprice.com/story-%d</link><pubDate>Tue, 10 Nov 2026 12:00:00 +0000</pubDate></item>' % (title, index))
        feed = ('<rss><channel>' + ''.join(items) + '</channel></rss>').encode()
        requests = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass
            def get(self, url, **kwargs):
                requests.append(url)
                if url.endswith('/feed'):
                    return feed
                return b'<html><p>This original energy article contains concrete published facts and a sufficiently long paragraph for a verified source fixture.</p></html>'

        with patch.object(research, 'Client', FakeClient), patch.object(research, 'FEEDS', [('fixture', 'https://oilprice.com/feed')]):
            result = research.acquire_news(self.path, cutoff.date(), cutoff, budget=5)
        self.assertGreaterEqual(sum(a['suggested_section'] == 'market_news' for a in result['articles']), 3)
        self.assertGreaterEqual(sum(a['suggested_section'] == 'company_news' for a in result['articles']), 5)
        self.assertLessEqual(len(requests), 11)  # do not download all 20 originals
        compact = common.load(self.path / 'summary-tasks.json')
        self.assertEqual(len(compact['market_news']) + len(compact['company_news']), 8)

    def test_public_gzip_is_decoded_with_size_bound(self):
        payload = b'<html><p>Verified news body</p></html>'
        class Response(io.BytesIO):
            headers = {'Content-Encoding': 'gzip'}
        client = common.Client(time.monotonic() + 5)
        with patch.object(client.opener, 'open', return_value=Response(gzip.compress(payload))):
            self.assertEqual(client.get('https://example.com/news', compressed=True), payload)
        client = common.Client(time.monotonic() + 5)
        with patch.object(client.opener, 'open', return_value=Response(gzip.compress(b'a' * 10000))):
            with self.assertRaisesRegex(common.Failure, 'SOURCE_RESPONSE_TOO_LARGE_OR_INVALID'):
                client.get('https://example.com/news', compressed=True, limit=1000)


if __name__ == '__main__':
    unittest.main()
