"""Offline news-stage handoff regressions for the unpublished 2.1.3 candidate."""
import argparse
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.4'
sys.path.insert(0, str(SKILL / 'scripts'))
import daily
from common import Failure, digest, load, save

assert Path(daily.__file__).resolve() == SKILL / 'scripts/daily.py'


class NewsStageReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='energy-news-stage-')
        self.project = Path(self.temporary.name)
        self.args = argparse.Namespace(workspace=str(self.project), date=None, news_as_of=None)
        self.network = patch.object(daily.research.Client, 'get', side_effect=AssertionError('No provider request permitted'))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.temporary.cleanup()

    def seed(self, market_count, company_count, prepared=True):
        articles = []
        for section, count in (('market_news', market_count), ('company_news', company_count)):
            for index in range(count):
                key = section + str(index)
                body = 'Synthetic archived original article with factual details for an offline source-evidence fixture.'
                path = self.project / 'sources' / (key + '.html')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('<html><p>' + body + '</p></html>', encoding='utf-8')
                articles.append({'id': key, 'url': 'https://example.com/' + key, 'source': 'fixture',
                                 'original_title': 'Synthetic original title ' + key, 'body': body,
                                 'suggested_section': section, 'published_at': '2026-11-13T08:00:00+08:00',
                                 'evidence': ['sources/' + path.name], 'evidence_sha256': digest(path.read_bytes())})
        catalog = {'publication_date': '2026-11-14', 'news_as_of': '2026-11-14T07:00:00+08:00', 'articles': articles}
        save(self.project / 'market.json', {'market_complete': True, 'freight': []})
        save(self.project / 'financial.json', {'stocks': [], 'fx': []})
        save(self.project / 'research.json', catalog)
        daily.research.write_summary_tasks(self.project, catalog)
        if prepared:
            save(self.project / 'PROJECT.json', {'version': '2.1.2-portable', 'publication_date': '2026-11-14',
                 'data_date': '2026-11-13', 'news_as_of': catalog['news_as_of'],
                 'input_hashes': {key: digest((self.project / file).read_bytes()) for key, file in
                                  (('market', 'market.json'), ('financial', 'financial.json'), ('research', 'research.json'))}})

    def initial(self):
        files = {'market': 'market.json', 'finance': 'financial.json', 'news': 'research.json'}
        def acquire(name, project, publication, cutoff, args):
            return load(project / files[name])
        self.args.date = '2026-11-14'
        self.args.news_as_of = '2026-11-14T07:00:00+08:00'
        with patch.object(daily, 'acquisition_job', side_effect=acquire), contextlib.redirect_stderr(io.StringIO()):
            return daily.begin(self.args)

    def reuse(self):
        with patch.object(daily, 'acquisition_job', side_effect=AssertionError('Prepared sources must not be recollected')):
            return daily.begin(self.args)

    def add(self, section='company_news', body=None):
        page = self.project / 'sources/addition.html'
        page.parent.mkdir(parents=True, exist_ok=True)
        text = body if body is not None else 'Synthetic independent company announcement with enough archived original facts for this offline test.'
        page.write_text('<html><title>Synthetic original supplement</title><meta property="article:published_time" '
                        'content="2026-11-13T09:00:00+08:00"><p>' + text + '</p></html>', encoding='utf-8')
        path = self.project / 'addition.json'
        save(path, [{'url': 'https://example.com/new-source', 'source': 'fixture', 'section': section,
                     'published_at': '2026-11-13T09:00:00+08:00', 'evidence': 'sources/addition.html'}])
        return argparse.Namespace(workspace=str(self.project), sources=str(path))

    def assert_missing(self, receipt, market, company):
        self.assertEqual(receipt['status'], 'NEWS_SUPPLEMENT_REQUIRED')
        self.assertEqual(receipt['news_deficits'], {'market_news': market, 'company_news': company})
        self.assertEqual(receipt['required_action'], 'supplement_news')
        self.assertEqual(receipt['next_action'], 'fetch_original_pages_then_import_news')
        self.assertIsNone(receipt['next_command'])
        self.assertFalse(receipt['completion_confirmed'])
        self.assertFalse(receipt['publication_allowed'])
        self.assertTrue(receipt['news_supplement_required'])
        self.assertEqual(Path(receipt['evidence_protocol']), SKILL / 'references/evidence.md')

    def assert_summaries(self, receipt, status):
        self.assertEqual(receipt['status'], status)
        self.assertEqual(receipt['news_deficits'], {'market_news': 0, 'company_news': 0})
        self.assertEqual(receipt['required_action'], 'write_news_summaries')
        self.assertEqual(receipt['next_command'], daily.command('build', self.project))
        self.assertTrue(receipt['next_command_requires_news_file'])
        self.assertFalse(receipt['completion_confirmed'])
        self.assertFalse(receipt['publication_allowed'])

    def test_initial_deficit_never_suggests_build(self):
        self.seed(1, 4, prepared=False)
        self.assert_missing(self.initial(), 2, 1)

    def test_initial_sufficient_candidates_require_summaries_not_publication(self):
        self.seed(3, 5, prepared=False)
        self.assert_summaries(self.initial(), 'READY_FOR_NEWS_SUMMARIES')

    def test_reused_incomplete_project_preserves_sources_and_deficit(self):
        self.seed(2, 3)
        files = ('market.json', 'financial.json', 'research.json', 'PROJECT.json')
        before = {name: (self.project / name).read_bytes() for name in files}
        receipt = self.reuse()
        self.assert_missing(receipt, 1, 2)
        self.assertFalse(receipt['collection_restarted'])
        self.assertEqual(before, {name: (self.project / name).read_bytes() for name in files})

    def test_reused_sufficient_project_keeps_compatibility_status(self):
        self.seed(3, 5)
        self.assert_summaries(self.reuse(), 'SOURCES_ALREADY_PREPARED')

    def test_reused_project_reconstructs_missing_batch_and_still_exposes_deficit(self):
        self.seed(0, 5)
        (self.project / 'summary-tasks.json').unlink()
        self.assert_missing(self.reuse(), 3, 0)

    def test_import_insufficient_evidence_does_not_advance_to_build(self):
        self.seed(2, 3)
        receipt = daily.import_news(self.add())
        self.assertEqual(receipt['added'], 1)
        self.assert_missing(receipt, 1, 1)

    def test_import_completes_candidates_preserving_quote_hashes(self):
        self.seed(3, 4)
        before = load(self.project / 'PROJECT.json')['input_hashes']
        self.assert_summaries(daily.import_news(self.add()), 'VERIFIED_SOURCE_EVIDENCE_IMPORTED')
        after = load(self.project / 'PROJECT.json')['input_hashes']
        self.assertEqual(before['market'], after['market'])
        self.assertEqual(before['financial'], after['financial'])
        self.assertNotEqual(before['research'], after['research'])

    def test_import_still_rejects_missing_original_body(self):
        self.seed(3, 4)
        before = (self.project / 'research.json').read_bytes()
        with self.assertRaisesRegex(Failure, 'NEWS_ORIGINAL_BODY_UNAVAILABLE'):
            daily.import_news(self.add(body='Too short.'))
        self.assertEqual(before, (self.project / 'research.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
