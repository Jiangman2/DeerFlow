"""Test the new link and the complete prior programmatic build/news suites."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

REPO=Path(__file__).resolve().parents[3]
SKILL=REPO/'portable-skills/candidates/energy-market-daily-2.1.5'
sys.path.insert(0,str(SKILL/'scripts'))
import delivery_text as delivery
from common import Failure

URL='https://cnoocclaw.cnooc.com.cn/workspace/chats/11111111-2222-4333-8444-555555555555'
READER='https://cnoocclaw.cnooc.com.cn/reports/energy-daily/11111111-2222-4333-8444-555555555555/2026-11-14.html'
DATA={'publication_date':'2026-11-14','data_date':'2026-11-13','quality':'完整'}
TEXT='油气与金融市场日报\n发布日期：2026-11-14；行情日期：2026-11-13\n报告状态：完整\n'


class HtmlLinkTests(unittest.TestCase):
    def test_host_identity_and_publication_date_determine_reader(self):
        self.assertEqual(delivery.html_report_url({'conversation_url':URL},'2026-11-14'),READER)

    def test_no_context_does_not_invent_link(self):
        self.assertIsNone(delivery.html_report_url({},'2026-11-14'))
        self.assertEqual(delivery.report_text(DATA,TEXT,{}),TEXT)

    def test_unsafe_contexts_cannot_create_links(self):
        for value in ['http://example.com/workspace/chats/a','https://example.com/drive/home/',
                      URL+'?next=evil',URL+'#fragment','https://127.0.0.1/workspace/chats/a',
                      'https://user:password@example.com/workspace/chats/a',URL+'/../a']:
            with self.subTest(value=value):
                self.assertIsNone(delivery.html_report_url({'conversation_url':value},'2026-11-14'))

    def test_invalid_date_cannot_enter_route(self):
        for value in ['2026-02-30','2026-1-1','2026-11-14/../a',None]:
            with self.subTest(value=value),self.assertRaises(Failure):
                delivery.html_report_url({'conversation_url':URL},value)

    def test_both_links_in_one_text_without_publication_claim(self):
        value=delivery.report_text(DATA,TEXT,{'conversation_url':URL},include_html_link=True)
        self.assertEqual(value.count('[点击查看日报网页]('+READER+')'),1)
        self.assertEqual(value.count('[点击查看完整日报]('+URL+')'),1)
        self.assertNotIn('已发布',value)
        self.assertEqual(delivery.report_text(DATA,value,{'conversation_url':URL},include_html_link=True),value)

    def test_old_footer_can_upgrade_once(self):
        old=delivery.report_text(DATA,TEXT,{'conversation_url':URL},include_html_link=False)
        value=delivery.report_text(DATA,old,{'conversation_url':URL},include_html_link=True)
        self.assertEqual(value.count('[点击查看日报网页]'),1)
        self.assertEqual(value.count('[点击查看完整日报]'),1)

    def test_conflicting_embedded_link_rejected(self):
        with self.assertRaisesRegex(Failure,'DELIVERY_TEXT_LINK_CONFLICT'):
            delivery.report_text(DATA,TEXT+'\n[点击查看日报网页](https://evil.invalid)\n',{'conversation_url':URL})

    def test_business_modules_unchanged(self):
        for name in ['common.py','market.py','research.py','report_renderer.py','browser_runtime.py']:
            self.assertEqual((SKILL/'scripts'/name).read_bytes(),(SKILL.with_name('energy-market-daily-2.1.4')/'scripts'/name).read_bytes())


def suite():
    result=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(HtmlLinkTests)])
    for name in ['test_energy_html_delivery_20261114.py','test_energy_html_news_stage_20261114.py']:
        path=REPO/'portable-skills/tests'/name
        source=path.read_text(encoding='utf-8').replace("candidates/energy-market-daily-2.1.4'","candidates/energy-market-daily-2.1.5'")
        module=types.ModuleType('_reader_'+path.stem);module.__file__=str(path)
        exec(compile(source,str(path),'exec'),module.__dict__)
        result.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    return result


if __name__=='__main__':
    outcome=unittest.TextTestRunner(verbosity=1).run(suite())
    sys.exit(not outcome.wasSuccessful())
