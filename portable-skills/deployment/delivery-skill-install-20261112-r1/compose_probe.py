"""Sealable compose-only qualification; synthetic local files, never a sender."""

import copy
import hashlib
import json
from pathlib import Path
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
    return {
        'status': 'COMPOSE_ONLY_SUCCESS_AND_MISSING_CASES_QUALIFIED',
        'synthetic_projection_used': True,
        'real_publication_proven': False,
        'complete_artifacts_accepted': True,
        'missing_artifact_rejected': True,
        'missing_projection_marked_unconfirmed': True,
        'missing_data_marked': True,
        'accepted_four_files_unchanged': True,
        'messages_sent': 0,
        'network_requests': 0,
        'final_text_sha256': complete['final_text_sha256'],
    }
