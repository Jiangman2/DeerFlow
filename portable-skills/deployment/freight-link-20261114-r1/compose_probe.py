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
    # This function is called only after the fixed PNG hash was independently
    # matched. Accepting that synthetic fixture is not user/business acceptance.
    project = Path(project)
    require(project.is_relative_to(Path(temporary)), 'SYNTHETIC_PROJECT_REQUIRED')
    before = {item['path']: digest(item['path']) for item in report['files']}
    accepted = daily.accept(SimpleNamespace(workspace=str(project), visual_reviewed=True))
    require(accepted['status'] == 'REPORT_ACCEPTED', 'SYNTHETIC_REPORT_NOT_ACCEPTED')
    original_text = Path(accepted['reply_text_file']).read_text(encoding='utf-8')
    thread_id = '11111111-1111-4111-8111-111111111111'
    url = 'https://example.invalid/workspace/chats/' + thread_id
    directory = project / 'delivery'
    directory.mkdir(exist_ok=True)
    projection = directory / 'synthetic-host-publication.json'
    projection.write_text(json.dumps({
        'name': 'present_files', 'status': 'success',
        'tool_call_id': 'synthetic-publication-only', 'thread_id': thread_id,
        'content': 'Successfully presented files\nconversation_url: ' + url,
        'requested_files': [item['path'] for item in accepted['files']],
    }), encoding='utf-8')
    args = SimpleNamespace(workspace=str(project), publication_receipt=str(projection))
    complete = daily.final_text(args)
    text_path = Path(complete['final_text_file'])
    text = text_path.read_text(encoding='utf-8')
    require(complete['status'] == 'FINAL_TEXT_READY' and complete['messages_sent'] == 0
            and complete['publication_confirmed_by_caller_projection'] is True
            and complete['conversation_url'] == url
            and text.startswith(original_text)
            and text.count('[点击查看完整日报](' + url + ')') == 1, 'COMPLETE_COMPOSE_CASE_FAILED')
    require(digest(text_path) == complete['final_text_sha256'], 'FINAL_TEXT_HASH_CHANGED')
    failed_projection = json.loads(projection.read_text(encoding='utf-8'))
    failed_projection.update(status='error', content='Error: failed to present files\nFile delivery incomplete. conversation_url: ' + url)
    failed_projection['requested_files'] = failed_projection['requested_files'][:1]
    projection.write_text(json.dumps(failed_projection), encoding='utf-8')
    partial = daily.final_text(args)
    partial_text = Path(partial['final_text_file']).read_text(encoding='utf-8')
    require(partial['publication_confirmed_by_caller_projection'] is False and partial['conversation_url'] == url
            and partial_text.endswith('[点击查看完整日报](' + url + ')\n')
            and '附件发布状态尚未确认' in partial_text, 'CONVERSATION_LINK_WITHOUT_PUBLICATION_FAILED')
    failed_projection['status'] = 'success'
    failed_projection['content'] = 'Successfully presented files\nconversation_url: ' + url
    failed_projection['requested_files'] = [item['path'] for item in accepted['files']]
    projection.write_text(json.dumps(failed_projection), encoding='utf-8')
    # Missing a host projection is a truthful unconfirmed handoff, not a guessed
    # platform URL. All report files remain present for this separate case.
    unconfirmed = daily.final_text(SimpleNamespace(workspace=str(project), publication_receipt=None))
    require(unconfirmed['status'] == 'FINAL_TEXT_READY' and unconfirmed['messages_sent'] == 0
            and unconfirmed['publication_confirmed_by_caller_projection'] is False
            and unconfirmed['conversation_url'] is None
            and '[点击查看完整日报](' not in Path(unconfirmed['final_text_file']).read_text(encoding='utf-8'),
            'UNCONFIRMED_COMPOSE_CASE_FAILED')
    complete = daily.final_text(args)
    final_before_missing = text_path.read_bytes()
    image = next(Path(item['path']) for item in accepted['files'] if item['path'].endswith('.png'))
    hidden = image.with_name('.synthetic-withheld.png')
    image.rename(hidden)
    try:
        try:
            daily.final_text(args)
        except daily.Failure as exc:
            require(exc.code == 'OUTPUT_CHANGED', 'MISSING_ARTIFACT_ERROR_CHANGED')
        else:
            raise ValueError('MISSING_ARTIFACT_WAS_ACCEPTED')
        require(text_path.read_bytes() == final_before_missing, 'FAILED_COMPOSE_OVERWROTE_FINAL_TEXT')
    finally:
        hidden.rename(image)
    # Check the missing-market-data wording without fetching sources, changing
    # prepared evidence, rewriting accepted report files, or rendering again.
    from delivery_text import compose
    data = daily.snapshot(project)
    missing = copy.deepcopy(data)
    missing['quality'] = '数据不完整'
    missing['oil'][0].update(value=None, status='missing', missing_reason='合成资格检查缺值')
    missing_text = compose(missing, daily.reply_text(missing), {'confirmed': False, 'conversation_url': None})
    require('数据不完整' in missing_text and '数据未取得' in missing_text
            and '未用旧价冒充目标日行情' in missing_text, 'MISSING_DATA_NOTE_FAILED')
    require(before == {item['path']: digest(item['path']) for item in accepted['files']}, 'ACCEPTED_ARTIFACTS_CHANGED')
    fallback_project = make_freight_project(daily, project, temporary, 'freight-compose')
    fallback = daily.snapshot(fallback_project)
    assert_freight_dates(daily, fallback, daily.load(project / 'market.json')['freight'])
    return {
        'status': 'COMPOSE_ONLY_SUCCESS_AND_MISSING_CASES_QUALIFIED',
        'synthetic_projection_used': True,
        'real_publication_proven': False,
        'complete_artifacts_accepted': True,
        'missing_artifact_rejected': True,
        'missing_projection_marked_unconfirmed': True,
        'missing_data_marked': True,
        'accepted_four_files_unchanged': True,
        'conversation_link_without_publication': True,
        'freight_fallback_date_marked': True,
        'messages_sent': 0,
        'network_requests': 0,
        'final_text_sha256': complete['final_text_sha256'],
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
    paths = {Path(item['path']).suffix: Path(item['path']) for item in report['files']}
    actual = daily.load(paths['.json'])
    require(actual['freight'] == data['freight'], 'PERSISTED_FREIGHT_DATA_CHANGED')
    assert_freight_dates(daily, actual, original_quotes, paths['.html'].read_text(encoding='utf-8'),
                        paths['.txt'].read_text(encoding='utf-8'))
    image = next(item for item in report['files'] if item['path'].endswith('.png'))
    with paths['.png'].open('rb') as stream:
        header = stream.read(24)
    require(header[:8] == b'\x89PNG\r\n\x1a\n' and len(header) == 24
            and struct.unpack('>II', header[16:24]) == (780, report['png']['height']), 'FREIGHT_PNG_HEADER_INVALID')
    require(image['bytes'] > 1000 and report['png']['width'] == 780 and digest(image['path']) == image['sha256'],
            'FREIGHT_FALLBACK_PNG_INVALID')
    return {'status': 'FREIGHT_FALLBACK_RENDER_QUALIFIED', 'actual_dates_verified': True,
            'png_sha256': image['sha256'], 'png_bytes': image['bytes'], 'png': report['png'],
            'render_seconds': report['render_seconds'], 'provider_requests': 0,
            'visual_business_acceptance_pending': True}
