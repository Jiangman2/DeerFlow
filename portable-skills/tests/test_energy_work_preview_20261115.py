"""Offline 2.1.3 programmatic acceptance and one-publication handoff regressions."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.6'
sys.path.insert(0, str(SKILL / 'scripts'))
import daily
import delivery_text as delivery
from common import Failure, load, save
assert Path(daily.__file__).resolve() == SKILL / 'scripts/daily.py'
fixture_path = Path(__file__).with_name('test_energy_market_daily.py')
source = fixture_path.read_text(encoding='utf-8')
marker = "SKILL = Path(__file__).resolve().parents[1] / 'energy-market-daily'"
assert source.count(marker) == 1
source = source.replace(marker, 'SKILL = Path(' + repr(str(SKILL)) + ')')
fixtures = types.ModuleType('programmatic_fixtures'); fixtures.__file__ = str(fixture_path)
exec(compile(source, str(fixture_path), 'exec'), fixtures.__dict__)
URL = 'https://cnoocclaw.cnooc.com.cn/workspace/chats/1a749b15-a6dc-4fa6-99a3-7a3b2ed195c4'
OTHER = 'https://cnoocclaw.cnooc.com.cn/workspace/chats/7f645bba-9bfd-4786-ab0a-e8636cf255d3'


def png_bytes(width=780, height=1200):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    row = (bytes(range(256)) * ((width * 3 + 255) // 256))[:width * 3]
    raw = (b'\0' + row) * height
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def checks(hidden=False):
    return {'basis': daily.PROGRAMMATIC_BASIS, 'dom_sections_verified': 7, 'news_content_verified': True,
            'quote_set_verified': True, 'missing_data_label_verified': True, 'fonts_loaded': True,
            'model_visual_review_used': False, 'ocr_used': False, 'viewport_widths_without_overflow': [320, 390, 430],
            'png_structure_verified': True, 'global_warning_hidden_in_html_and_png': hidden}


def mock_png(path, data=None):
    path.with_suffix('.png').write_bytes(png_bytes())
    return {**daily.verify_png_structure(path.with_suffix('.png')), 'browser_fallback_count': 0,
            'programmatic_checks': checks(not data['formal_report_ready'] if data else False)}


class ProgrammaticBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='energy-213-'); self.project = Path(self.temp.name)
        fixtures.project_fixture(self.project)
        self.args = types.SimpleNamespace(workspace=str(self.project), visual_reviewed=True, publication_receipt=None)
        self.env = patch.dict(os.environ, {delivery.CONVERSATION_ENV: URL}); self.env.start()
        self.png = patch.object(daily, 'png', side_effect=mock_png); self.browser = self.png.start()

    def tearDown(self):
        self.png.stop(); self.env.stop(); self.temp.cleanup()

    def test_build_accepts_once_without_claiming_visual_or_publication(self):
        receipt = daily.build(self.args)
        self.assertEqual(receipt['status'], 'REPORT_ACCEPTED')
        self.assertEqual(receipt['acceptance_basis'], daily.PROGRAMMATIC_BASIS)
        self.assertFalse(receipt['visual_reviewed']); self.assertFalse(receipt['external_delivery_ready'])
        self.assertTrue(receipt['publication_allowed']); self.assertTrue(receipt['delivery_requires_successful_publication'])
        self.assertEqual(receipt['next_action'], 'publish_files_then_reply_once')
        self.assertNotIn('next_command', receipt)
        self.assertEqual(len(receipt['files']), 4)
        self.assertTrue(Path(receipt['final_text_file']).read_text(encoding='utf-8').endswith('[点击查看完整日报](' + URL + ')\n'))

    def test_same_project_reuses_one_verified_generation(self):
        first = daily.build(self.args); second = daily.build(self.args)
        self.assertTrue(second['reused']); self.assertEqual(self.browser.call_count, 1)
        self.assertEqual(first['outputs_generation'], second['outputs_generation'])

    def test_missing_png_fails_before_result(self):
        self.browser.side_effect = lambda path, data: {'width': 780, 'height': 1200}
        with self.assertRaisesRegex(Failure, 'GENERATED_FILE_SET_INVALID'): daily.build(self.args)
        self.assertFalse((self.project / 'RESULT.json').exists())

    def test_missing_four_file_member_is_not_accepted(self):
        for extension in ('.html', '.json', '.txt'):
            with self.subTest(extension=extension):
                def incomplete(path, data):
                    result = mock_png(path, data); path.with_suffix(extension).unlink(); return result
                self.browser.side_effect = incomplete
                with self.assertRaisesRegex(Failure, 'GENERATED_FILE_SET_INVALID'): daily.build(self.args)
                self.assertFalse((self.project / 'RESULT.json').exists())

    def test_inputs_changed_during_render_cannot_replace_old_accepted_result(self):
        daily.build(self.args); before = (self.project / 'RESULT.json').read_bytes()
        news = load(self.project / 'news.json'); news['market_news'][0]['text'] += ' 触发一次新构建。'
        save(self.project / 'news.json', news)
        def mutate(path, data):
            result = mock_png(path, data)
            changed = load(self.project / 'news.json'); changed['market_news'][0]['text'] += ' 生成期间又改变。'
            save(self.project / 'news.json', changed); return result
        self.browser.side_effect = mutate
        with self.assertRaisesRegex(Failure, 'RENDERED_INPUT_CHANGED'): daily.build(self.args)
        self.assertEqual((self.project / 'RESULT.json').read_bytes(), before)

    def test_missing_current_url_never_becomes_send_ready(self):
        os.environ.pop(delivery.CONVERSATION_ENV)
        receipt = daily.build(self.args)
        self.assertEqual(len(receipt['files']), 4)
        self.assertEqual(receipt['next_action'], 'publish_files_without_external_send')
        self.assertFalse(receipt['reply_text_ready'])
        self.assertNotIn('点击查看完整日报', Path(receipt['final_text_file']).read_text(encoding='utf-8'))

    def test_cross_thread_reuse_refused_without_new_png_or_mutation(self):
        daily.build(self.args); before = (self.project / 'RESULT.json').read_bytes()
        os.environ[delivery.CONVERSATION_ENV] = OTHER
        with self.assertRaisesRegex(Failure, 'PROJECT_CONVERSATION_MISMATCH'): daily.build(self.args)
        self.assertEqual(self.browser.call_count, 1); self.assertEqual((self.project / 'RESULT.json').read_bytes(), before)

    def test_programmatic_receipt_cannot_omit_real_checks(self):
        receipt = daily.build(self.args)
        for mutation in ({'programmatic_checks_passed': False}, {'acceptance_basis': 'VISUAL'}, {'publication_allowed': False}):
            with self.assertRaisesRegex(Failure, 'RESULT_ACCEPTANCE_INVALID'):
                daily.validate_result(dict(receipt, **mutation), self.project)
        invalid = copy.deepcopy(receipt); invalid['png']['programmatic_checks']['viewport_widths_without_overflow'] = [390]
        with self.assertRaisesRegex(Failure, 'REPORT_PROGRAMMATIC_CHECKS_REQUIRED'): daily.validate_result(invalid, self.project)
        invalid = copy.deepcopy(receipt); invalid['png']['programmatic_checks']['global_warning_hidden_in_html_and_png'] = True
        with self.assertRaisesRegex(Failure, 'RESULT_SCREENSHOT_MODE_MISMATCH'): daily.validate_result(invalid, self.project)

    def test_optional_visual_review_is_explicit_and_keeps_programmatic_basis(self):
        daily.build(self.args)
        with self.assertRaisesRegex(Failure, 'ACTUAL_VISUAL_REVIEW_REQUIRED'):
            daily.accept(types.SimpleNamespace(workspace=str(self.project), visual_reviewed=False))
        receipt = daily.accept(self.args)
        self.assertTrue(receipt['visual_reviewed']); self.assertEqual(receipt['acceptance_basis'], daily.PROGRAMMATIC_BASIS)

    def test_legacy_final_text_accepts_new_programmatic_receipt_without_fake_visual(self):
        receipt = daily.build(self.args); result = daily.final_text(self.args)
        self.assertEqual(result['status'], 'FINAL_TEXT_READY')
        self.assertEqual(Path(result['final_text_file']).read_bytes(), Path(receipt['reply_text_file']).read_bytes())
        self.assertFalse(load(self.project / 'RESULT.json')['visual_reviewed'])

    def test_png_corruption_or_truncation_is_rejected(self):
        image = self.project / 'synthetic.png'; valid = png_bytes(); image.write_bytes(valid)
        self.assertTrue(daily.verify_png_structure(image)['structure_verified'])
        for content in (valid[:-1], valid[:-12], valid[:-30] + b'X' + valid[-29:], valid + b'x'):
            with self.subTest(length=len(content)):
                image.write_bytes(content)
                with self.assertRaises(Failure): daily.verify_png_structure(image)

    def test_core_five_files_are_byte_identical_to_published_212(self):
        original = SKILL.parent / 'energy-market-daily-2.1.2/scripts'
        for name in ('common.py', 'market.py', 'research.py', 'report_renderer.py', 'browser_runtime.py'):
            self.assertEqual((SKILL / 'scripts' / name).read_bytes(), (original / name).read_bytes(), name)

    def old_212_receipt(self):
        """Exercise the immutable published source, rather than relabeling 2.1.3."""
        previous = SKILL.parent / 'energy-market-daily-2.1.2/scripts/daily.py'
        legacy = types.ModuleType('published_daily_212'); legacy.__file__ = str(previous)
        saved_path = list(sys.path)
        try:
            exec(compile(previous.read_bytes(), str(previous), 'exec'), legacy.__dict__)
        finally:
            sys.path[:] = saved_path
        state = load(self.project / 'PROJECT.json'); state['version'] = '2.1.2-portable'
        save(self.project / 'PROJECT.json', state)
        def legacy_png(path):
            path.with_suffix('.png').write_bytes(png_bytes())
            return {'width': 780, 'height': 1200, 'browser_fallback_count': 0}
        with patch.object(legacy, 'png', side_effect=legacy_png):
            legacy.build(self.args)
            receipt = legacy.accept(self.args)
        self.assertEqual(receipt['version'], '2.1.2-portable')
        self.assertTrue(receipt['visual_reviewed'])
        return receipt

    def test_real_212_accept_stops_with_operator_build_handoff(self):
        self.old_212_receipt()
        before = {path.relative_to(self.project): path.read_bytes() for path in self.project.rglob('*') if path.is_file()}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = daily.main(['accept', '--workspace', str(self.project), '--visual-reviewed'])
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'REPORT_REBUILD_FOR_CURRENT_DELIVERY_REQUIRED')
        self.assertEqual(result['next_action'], 'stop_and_report_legacy_rebuild_required')
        self.assertEqual(result['resume_command'], daily.command('build', self.project))
        self.assertEqual(result['failed_owner_action'], 'accept')
        self.assertTrue(result['owner_handoff_required'])
        self.assertFalse(result['retry_allowed']); self.assertFalse(result['automatic_owner_handoff'])
        self.assertFalse(result['visual_review_claim_created']); self.assertNotIn('next_command', result)
        self.assertEqual({path.relative_to(self.project): path.read_bytes() for path in self.project.rglob('*') if path.is_file()}, before)
        self.assertEqual(self.browser.call_count, 0)

    def test_real_212_legacy_final_text_preserves_original_content_checks(self):
        receipt = self.old_212_receipt()
        result = daily.final_text(self.args)
        self.assertEqual(result['status'], 'FINAL_TEXT_READY')
        final = Path(result['final_text_file']); before = final.read_bytes()
        text = Path(receipt['reply_text_file'])
        text.write_text(text.read_text(encoding='utf-8').replace(URL, OTHER), encoding='utf-8')
        for item in receipt['files']:
            if item['path'] == str(text):
                item.update(bytes=text.stat().st_size, sha256=daily.digest(text.read_bytes()))
        save(self.project / 'RESULT.json', receipt)
        with self.assertRaisesRegex(Failure, 'RESULT_REPLY_TEXT_MISMATCH'): daily.final_text(self.args)
        self.assertEqual(final.read_bytes(), before)

    def test_real_212_receipt_cannot_redirect_text_or_lose_acceptance_flag(self):
        receipt = self.old_212_receipt()
        daily.validate_result(receipt, self.project)
        with self.assertRaisesRegex(Failure, 'RESULT_REPLY_TEXT_IDENTITY'):
            daily.validate_result(dict(receipt, reply_text_file=str(self.project / 'unrelated.txt')), self.project)
        with self.assertRaisesRegex(Failure, 'RESULT_ACCEPTANCE_INVALID'):
            daily.validate_result(dict(receipt, visual_reviewed=False), self.project)


class FakePage:
    def __init__(self, data, overflow=None, bad_banner=False):
        self.data=data; self.overflow=overflow; self.bad_banner=bad_banner
        self.width=390; self.width_checks=[]; self.styles=[]; self.closed=False
    def route(self, *args): pass
    def goto(self, *args, **kwargs): pass
    def add_style_tag(self, *, content): self.styles.append(content)
    def set_viewport_size(self, size): self.width=size['width']
    def evaluate(self, script, facts=None):
        if script=='document.fonts.ready': return None
        if script=='document.documentElement.scrollWidth <= window.innerWidth':
            self.width_checks.append(self.width); return self.width != self.overflow
        if facts is None: return -1 if self.bad_banner else int(not self.data['formal_report_ready'])
        return {'titles':list(daily.REPORT_SECTIONS),'news_counts':[len(self.data[k]) for k in ('market_news','company_news')],
          'quote_count':sum(len(self.data[k]) for k in ('oil','gas','stocks','fx')),'freight_rows':len(self.data['freight'])//2,
          'all_sections_visible':True,'fonts_loaded':True,'news_text_present':True,'replacement_character_present':False,
          'incomplete_banner':not self.data['formal_report_ready'],'hidden_screenshot_banners':int(not self.data['formal_report_ready'])}
    def screenshot(self, *, path, **kwargs): Path(path).write_bytes(png_bytes())


class BrowserPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='energy-213-browser-'); self.project=Path(self.temp.name)
        fixtures.project_fixture(self.project); self.data=daily.snapshot(self.project)
        self.path=self.project/'report.html'; self.path.write_text(daily.render(self.data),encoding='utf-8')
    def tearDown(self): self.temp.cleanup()
    def run_png(self, page):
        browser=types.SimpleNamespace(new_page=lambda **kwargs:page,close=lambda:None)
        launch=unittest.mock.Mock(return_value=browser)
        runtime=types.SimpleNamespace(chromium=types.SimpleNamespace(launch=launch))
        module=types.ModuleType('playwright.sync_api'); module.sync_playwright=lambda:contextlib.nullcontext(runtime)
        with patch.dict(sys.modules,{'playwright.sync_api':module}), patch.object(daily,'runtime_browser',return_value=contextlib.nullcontext(None)), patch.object(daily,'browser_candidates',return_value=[]):
            result=daily.png(self.path,self.data)
        return result,launch
    def test_same_screenshot_mode_checks_all_three_widths_once(self):
        page=FakePage(self.data); result,launch=self.run_png(page)
        self.assertEqual(page.width_checks,[320,390,430]); self.assertEqual(launch.call_count,1)
        self.assertEqual(result['programmatic_checks']['viewport_widths_without_overflow'],[320,390,430])
        self.assertEqual(len(page.styles),1)
    def test_each_overflow_is_blocking_without_browser_fallback(self):
        for width in (320,390,430):
            page=FakePage(self.data,overflow=width)
            with self.subTest(width=width), self.assertRaisesRegex(Failure,'REPORT_HORIZONTAL_OVERFLOW'):
                self.run_png(page)
            self.assertEqual(page.width_checks[-1],width)
    def test_warning_is_hidden_in_delivered_html_and_png(self):
        self.data['formal_report_ready']=False; self.data['quality']='数据不完整'
        self.path.write_text(daily.render(self.data),encoding='utf-8'); before=self.path.read_bytes()
        page=FakePage(self.data); result,_=self.run_png(page)
        self.assertTrue(result['programmatic_checks']['global_warning_hidden_in_html_and_png'])
        self.assertIn('main.report > aside[data-energy-screenshot-quality-note="true"]',page.styles[0])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertIn('<aside hidden style="display:none;',before.decode())
        self.assertIn('数据不完整 · 本期含取数缺口',before.decode())
        self.assertIn('报告状态：数据不完整',daily.reply_text(self.data))
    def test_unknown_banner_shape_is_rejected_before_screenshot(self):
        with self.assertRaisesRegex(Failure,'REPORT_GLOBAL_WARNING_IDENTITY_INVALID'):
            self.run_png(FakePage(self.data,bad_banner=True))




LINK_URL='https://cnoocclaw.cnooc.com.cn/workspace/chats/11111111-2222-4333-8444-555555555555'
LINK_READER='https://cnoocclaw.cnooc.com.cn/reports/energy-daily/11111111-2222-4333-8444-555555555555/2026-11-14.html'
LINK_DATA={'publication_date':'2026-11-14','data_date':'2026-11-13','quality':'完整'}
LINK_TEXT='油气与金融市场日报\n发布日期：2026-11-14；行情日期：2026-11-13\n报告状态：完整\n'
class HtmlLinkTests(unittest.TestCase):
    def test_host_identity_and_publication_date_determine_reader(self):
        self.assertEqual(delivery.html_report_url({'conversation_url':LINK_URL},'2026-11-14'),LINK_READER)

    def test_no_context_does_not_invent_link(self):
        self.assertIsNone(delivery.html_report_url({},'2026-11-14'))
        self.assertEqual(delivery.report_text(LINK_DATA,LINK_TEXT,{}),LINK_TEXT)

    def test_unsafe_contexts_cannot_create_links(self):
        for value in ['http://example.com/workspace/chats/a','https://example.com/drive/home/',
                      LINK_URL+'?next=evil',LINK_URL+'#fragment','https://127.0.0.1/workspace/chats/a',
                      'https://user:password@example.com/workspace/chats/a',LINK_URL+'/../a']:
            with self.subTest(value=value):
                self.assertIsNone(delivery.html_report_url({'conversation_url':value},'2026-11-14'))

    def test_invalid_date_cannot_enter_route(self):
        for value in ['2026-02-30','2026-1-1','2026-11-14/../a',None]:
            with self.subTest(value=value),self.assertRaises(Failure):
                delivery.html_report_url({'conversation_url':LINK_URL},value)

    def test_both_links_in_one_text_without_publication_claim(self):
        value=delivery.report_text(LINK_DATA,LINK_TEXT,{'conversation_url':LINK_URL},include_html_link=True)
        self.assertEqual(value.count('[点击查看日报网页]('+LINK_READER+')'),1)
        self.assertEqual(value.count('[点击查看完整日报]('+LINK_URL+')'),1)
        self.assertNotIn('已发布',value)
        self.assertEqual(delivery.report_text(LINK_DATA,value,{'conversation_url':LINK_URL},include_html_link=True),value)

    def test_old_footer_can_upgrade_once(self):
        old=delivery.report_text(LINK_DATA,LINK_TEXT,{'conversation_url':LINK_URL},include_html_link=False)
        value=delivery.report_text(LINK_DATA,old,{'conversation_url':LINK_URL},include_html_link=True)
        self.assertEqual(value.count('[点击查看日报网页]'),1)
        self.assertEqual(value.count('[点击查看完整日报]'),1)

    def test_conflicting_embedded_link_rejected(self):
        with self.assertRaisesRegex(Failure,'DELIVERY_TEXT_LINK_CONFLICT'):
            delivery.report_text(LINK_DATA,LINK_TEXT+'\n[点击查看日报网页](https://evil.invalid)\n',{'conversation_url':LINK_URL})





class WorkPreviewTests(unittest.TestCase):
    setUp=ProgrammaticBuildTests.setUp
    tearDown=ProgrammaticBuildTests.tearDown

    def formatter(self, filename, names):
        import ast
        from urllib.parse import urlsplit
        from zoneinfo import ZoneInfo
        import datetime
        source=(Path(__file__).parent/'gateway-418'/filename).read_text(encoding='utf-8')
        selected=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
        scope={'re':__import__('re'),'urlsplit':urlsplit,'date':datetime.date,'datetime':datetime.datetime,'UTC':datetime.timezone.utc,
               'ZoneInfo':ZoneInfo,'validate_thread_id':lambda value:value}
        tree=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[]))
        exec(compile(tree,'<actual-gateway-418-formatters>','exec'),scope)
        return scope

    def notification(self,text):
        summary=self.formatter('work_notifications.py',{'_notification_summary'})['_notification_summary'](text)
        formatter=self.formatter('work_notification_delivery.py',{'_daily_reader_url','_message'})['_message']
        return formatter(types.SimpleNamespace(result_text=summary,thread_id=URL.rsplit('/',1)[1],title='日报',finished_at=''),'https://cnoocclaw.cnooc.com.cn')

    def test_build_text_keeps_its_reader_in_first_preview(self):
        report=daily.build(self.args)
        text=Path(report['final_text_file']).read_text(encoding='utf-8')
        expected='[点击查看日报网页]('+report['html_report_url']+')'
        self.assertEqual(text.splitlines()[0],expected)
        self.assertEqual(self.notification(text[:2000]),expected)
        self.assertEqual(self.notification(text[:2000][:2000]),expected)
        self.assertIn('油气与金融市场日报',text)

    def test_long_report_and_first_two_thousand_characters_yield_same_link(self):
        data=daily.snapshot(self.project)
        body=daily.reply_text(data)+'\n'+'已核实的新闻事实。'*900
        text=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True,link_first=True)
        self.assertGreater(len(text),2000)
        expected='[点击查看日报网页]('+delivery.html_report_url({'conversation_url':URL},data['publication_date'])+')'
        self.assertEqual(self.notification(text),expected)
        self.assertEqual(self.notification(text[:2000]),expected)
        self.assertIn(body.rstrip(),text)

    def test_current_final_text_compatibility_is_idempotent(self):
        report=daily.build(self.args);before={item['path']:Path(item['path']).read_bytes() for item in report['files']}
        result=daily.final_text(self.args)
        self.assertEqual(result['status'],'FINAL_TEXT_READY')
        text=Path(result['final_text_file']).read_text(encoding='utf-8')
        self.assertEqual(text.count('[点击查看日报网页]('),1)
        self.assertTrue(text.startswith('[点击查看日报网页]('))
        self.assertEqual(before,{path:Path(path).read_bytes() for path in before})

    def test_legacy_html_footer_format_remains_supported(self):
        data=daily.snapshot(self.project);body=daily.reply_text(data)
        old=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True)
        self.assertTrue(old.startswith('油气与金融市场日报'))
        self.assertEqual(delivery.report_text(data,old,{'conversation_url':URL},include_html_link=True),old)

    def test_other_conversation_header_is_rejected(self):
        data=daily.snapshot(self.project);body=daily.reply_text(data)
        text=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True,link_first=True)
        with self.assertRaises(Failure):delivery.report_text(data,text,{'conversation_url':OTHER},include_html_link=True,link_first=True)

