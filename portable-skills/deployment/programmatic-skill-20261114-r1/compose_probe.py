"""Sealable compose-only qualification; synthetic local files, never a sender."""

import copy
import datetime as dt
import hashlib
import html as html_module
import json
from pathlib import Path
import re
import shutil
import struct
from types import SimpleNamespace


def require(ok, code):
    if not ok:
        raise ValueError(code)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def qualify(daily, project, report, temporary):
    # Synthetic env only. The independent Gateway receipt proves deployment;
    # this probe never claims a real task, publication, webhook or Work send.
    import os
    from delivery_text import CONVERSATION_ENV, host_conversation_context
    project = Path(project)
    require(project.is_relative_to(Path(temporary)), 'SYNTHETIC_PROJECT_REQUIRED')
    url = 'https://example.invalid/workspace/chats/11111111-1111-4111-8111-111111111111'
    require(host_conversation_context()['conversation_url'] == url, 'SYNTHETIC_CONTEXT_REQUIRED')
    before = {item['path']: digest(item['path']) for item in report['files']}
    # Build itself performed the fixed-template checks. Do not invent a human
    # or model image review in this isolated qualification.
    daily.validate_result(report, project)
    accepted = report
    require(accepted['status'] == 'REPORT_ACCEPTED'
            and accepted['acceptance_basis'] == daily.PROGRAMMATIC_BASIS
            and accepted['programmatic_checks_passed'] is True
            and accepted['visual_reviewed'] is False
            and accepted['visual_review_required'] is False
            and accepted['next_action'] == 'publish_files_then_reply_once'
            and accepted['model_publication_receipt_transcription_required'] is False
            and accepted['final_text_command_required'] is False
            and 'after_publication_command' not in accepted
            and 'publication_projection_file' not in accepted,
            'SINGLE_PUBLICATION_HANDOFF_NOT_ACCEPTED')
    text_path = Path(accepted['reply_text_file'])
    require(accepted['final_text_file'] == str(text_path), 'ACCEPTED_TXT_IDENTITY')
    text = text_path.read_text(encoding='utf-8')
    data = daily.snapshot(project)
    require(text == daily.reply_text(data).rstrip() + '\n\n[点击查看完整日报](' + url + ')\n'
            and text.count(url) == 1 and accepted['reply_text_ready'] is True
            and accepted['ready_after_successful_publication'] is True
            and accepted['external_delivery_ready'] is False
            and accepted['delivery_requires_successful_publication'] is True,
            'SINGLE_LINKED_TEXT_NOT_READY')
    require(not (project / 'delivery').exists(), 'NORMAL_FLOW_CREATED_EXTRA_DELIVERY')
    image = next(Path(item['path']) for item in accepted['files'] if item['path'].endswith('.png'))
    hidden = image.with_name('.synthetic-withheld.png')
    image.rename(hidden)
    try:
        try:
            daily.validate_result(accepted, project)
        except daily.Failure as exc:
            require(exc.code == 'OUTPUT_CHANGED', 'MISSING_ARTIFACT_ERROR_CHANGED')
        else:
            raise ValueError('MISSING_ARTIFACT_WAS_ACCEPTED')
    finally:
        hidden.rename(image)
    # A legacy call without current env must not create or overwrite a draft,
    # even when accepted files already contain a previously valid URL.
    previous = os.environ.pop(CONVERSATION_ENV)
    try:
        missing_context = daily.final_text(SimpleNamespace(workspace=str(project), publication_receipt=None))
        require(missing_context['status'] == 'WAITING_FOR_CONVERSATION_LINK'
                and missing_context['reply_text_ready'] is False
                and missing_context['external_delivery_ready'] is False
                and missing_context['final_text_file'] is None, 'MISSING_CURRENT_CONTEXT_NOT_BLOCKED')
    finally:
        os.environ[CONVERSATION_ENV] = previous
    require(not (project / 'delivery').exists(), 'MISSING_CONTEXT_CREATED_DRAFT')
    require(before == {item['path']: digest(item['path']) for item in accepted['files']}, 'ACCEPTED_ARTIFACTS_CHANGED')
    fallback_project = make_freight_project(daily, project, temporary, 'freight-compose')
    fallback = daily.snapshot(fallback_project)
    assert_freight_dates(daily, fallback, daily.load(project / 'market.json')['freight'])
    return {
        'status': 'SINGLE_LINKED_TEXT_AND_MISSING_CONTEXT_QUALIFIED',
        'synthetic_host_environment_used': True,
        'real_host_context_injection_proven': False,
        'real_publication_proven': False,
        'complete_artifacts_accepted': True,
        'programmatic_acceptance_verified': True,
        'model_visual_review_used': False,
        'ocr_used': False,
        'missing_artifact_rejected': True,
        'missing_host_context_blocked': True,
        'accepted_four_files_unchanged': True,
        'single_text_ready_before_publication': True,
        'publication_transcription_required': False,
        'final_text_command_required': False,
        'freight_fallback_date_marked': True,
        'messages_sent': 0,
        'network_requests': 0,
        'final_text_sha256': digest(text_path),
    }


def make_freight_project(daily, project, temporary, name):
    """Copy only synthetic prepared inputs, then record dated missing/older quotes."""
    project, temporary = Path(project), Path(temporary)
    require(project.is_relative_to(temporary), 'SYNTHETIC_PROJECT_REQUIRED')
    target_project = temporary / name
    require(not target_project.exists(), 'SYNTHETIC_PROJECT_EXISTS')
    target_project.mkdir()
    for filename in ('market.json', 'financial.json', 'research.json', 'news.json', 'PROJECT.json'):
        shutil.copyfile(project / filename, target_project / filename)
    shutil.copytree(project / 'sources', target_project / 'sources')
    state = daily.load(target_project / 'PROJECT.json')
    original = daily.load(target_project / 'market.json')
    target = dt.date.fromisoformat(state['data_date'])
    records = []
    for kind in ('oil', 'gas', 'freight'):
        for index, quote in enumerate(original[kind]):
            current = dict(code=quote['code'], date=target.isoformat(), value=quote['value'], status='available',
                           unit=quote['unit'], item=quote.get('contract', quote['name']), raw_close=str(quote['value']))
            previous = dict(current, date=(target - dt.timedelta(days=4)).isoformat(), value=100.0, raw_close='100')
            records.append(previous)
            if kind == 'freight':
                records.append(dict(current, date=(target - dt.timedelta(days=1 + index % 2)).isoformat()))
                current.update(value=None, status='missing', raw_close='')
            records.append(current)
    energy = daily.market.build(daily.market.previous_months(target).isoformat(), target.isoformat(), records, {})
    daily.save(target_project / 'market.json', energy)
    state['version'] = daily.VERSION
    state['input_hashes']['market'] = digest(target_project / 'market.json')
    daily.save(target_project / 'PROJECT.json', state)
    return target_project


def assert_freight_dates(daily, data, original_quotes, rendered_html=None, rendered_text=None):
    from delivery_text import compose
    require(len(data['freight']) == 8 and all(q['status'] == 'latest_available' and q['date'] < data['data_date']
            and q['target_date'] == data['data_date'] for q in data['freight']), 'FREIGHT_FALLBACK_NOT_APPLIED')
    html = daily.render(data) if rendered_html is None else rendered_html
    text = daily.reply_text(data) if rendered_text is None else rendered_text
    final = compose(data, text, {'confirmed': False, 'conversation_url': None})
    table = re.search(r'<table class="freight-table">(.*?)</table>', html, re.S)
    require(table is not None, 'FREIGHT_TABLE_MISSING')
    cells = re.findall(r'<td>(.*?)</td>', table.group(1), re.S)
    require(len(cells) == len(original_quotes) == 8, 'FREIGHT_CELL_COUNT')
    target = dt.date.fromisoformat(data['data_date'])
    for index, (quote, original, cell) in enumerate(zip(data['freight'], original_quotes, cells)):
        expected_date = (target - dt.timedelta(days=1 + index % 2)).isoformat()
        require(quote['code'] == original['code'] and quote['value'] == original['value']
                and quote['date'] == expected_date, 'FREIGHT_REFERENCE_MAPPING_CHANGED')
        value = format(quote['value'], ',.' + str(quote.get('decimals', 2)) + 'f')
        require('<b>' + value + '</b>' in cell and expected_date in html_module.unescape(cell),
                'FREIGHT_CELL_DATE_OR_VALUE_MISMATCH')
        quote_line = '%s：%s %s' % (quote['name'], value, quote['unit'])
        following = text.split(quote_line, 1)
        require(len(following) == 2 and expected_date in following[1].splitlines()[1],
                'FREIGHT_TEXT_DATE_MAPPING_CHANGED')
        require(quote['date'] in final,
                'FREIGHT_DATE_MISSING_FROM_DELIVERABLE')
    require('最近已取得报价' in html and '最近已取得报价' in text and data['quality'] == '数据不完整',
            'FREIGHT_FALLBACK_MISREPRESENTED_AS_TARGET')


def qualify_freight_render(daily, project, temporary):
    """A second real render in the deployment's UID1000 public mount."""
    target = make_freight_project(daily, project, temporary, 'freight-render')
    data = daily.snapshot(target)
    original_quotes = daily.load(Path(project) / 'market.json')['freight']
    assert_freight_dates(daily, data, original_quotes)
    report = daily.build(SimpleNamespace(workspace=str(target)))
    daily.validate_result(report, target)
    require(report['status'] == 'REPORT_ACCEPTED'
            and report['acceptance_basis'] == daily.PROGRAMMATIC_BASIS
            and report['programmatic_checks_passed'] is True
            and report['visual_reviewed'] is False
            and report['png']['programmatic_checks']['global_warning_hidden_in_png_only'] is True,
            'FREIGHT_PROGRAMMATIC_ACCEPTANCE_REQUIRED')
    paths = {Path(item['path']).suffix: Path(item['path']) for item in report['files']}
    actual = daily.load(paths['.json'])
    require(actual['freight'] == data['freight'], 'PERSISTED_FREIGHT_DATA_CHANGED')
    assert_freight_dates(daily, actual, original_quotes, paths['.html'].read_text(encoding='utf-8'),
                        paths['.txt'].read_text(encoding='utf-8'))
    require('数据不完整 · 本期含取数缺口' in paths['.html'].read_text(encoding='utf-8')
            and actual['quality'] == '数据不完整'
            and '数据不完整' in paths['.txt'].read_text(encoding='utf-8'),
            'FREIGHT_FACTS_MUST_REMAIN_IN_NON_PNG_ARTIFACTS')
    image = next(item for item in report['files'] if item['path'].endswith('.png'))
    with paths['.png'].open('rb') as stream:
        header = stream.read(24)
    require(header[:8] == b'\x89PNG\r\n\x1a\n' and len(header) == 24
            and struct.unpack('>II', header[16:24]) == (780, report['png']['height']), 'FREIGHT_PNG_HEADER_INVALID')
    require(image['bytes'] > 1000 and report['png']['width'] == 780 and digest(image['path']) == image['sha256'],
            'FREIGHT_FALLBACK_PNG_INVALID')
    return {'status': 'FREIGHT_FALLBACK_RENDER_QUALIFIED', 'actual_dates_verified': True,
            'programmatic_acceptance_verified': True, 'model_visual_review_used': False,
            'global_warning_hidden_in_png_only': True, 'non_png_quality_facts_preserved': True,
            'png_sha256': image['sha256'], 'png_bytes': image['bytes'], 'png': report['png'],
            'render_seconds': report['render_seconds'], 'provider_requests': 0,
            'visual_business_acceptance_pending': True}
