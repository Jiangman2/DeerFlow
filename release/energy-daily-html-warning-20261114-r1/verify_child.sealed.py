"""Verify staged read-only Skill/PVC mounts and build the qualified fixed PNG.

No extraction, shared-directory writes, collection, business request or Work send.
"""
import base64
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import tempfile
import unittest

SKILL = Path('/mnt/skills/public/energy-market-daily')
RUNTIME = Path('/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64')
PACKAGE_SHA = 'b7b27e655f00f3d93202bdb2641403273493e0b3b18308127fded5f1ffcbd7f1'
CANDIDATE_VERSION = '2.1.4-portable'
CANDIDATE_FILES = 108
FINAL_TEXT_PROBE = '"""Sealable compose-only qualification; synthetic local files, never a sender."""\n\nimport copy\nimport datetime as dt\nimport hashlib\nimport html as html_module\nimport json\nfrom pathlib import Path\nimport re\nimport shutil\nimport struct\nfrom types import SimpleNamespace\n\n\ndef require(ok, code):\n    if not ok:\n        raise ValueError(code)\n\n\ndef digest(path):\n    return hashlib.sha256(Path(path).read_bytes()).hexdigest()\n\n\ndef qualify(daily, project, report, temporary):\n    # Synthetic env only. The independent Gateway receipt proves deployment;\n    # this probe never claims a real task, publication, webhook or Work send.\n    import os\n    from delivery_text import CONVERSATION_ENV, host_conversation_context\n    project = Path(project)\n    require(project.is_relative_to(Path(temporary)), \'SYNTHETIC_PROJECT_REQUIRED\')\n    url = \'https://example.invalid/workspace/chats/11111111-1111-4111-8111-111111111111\'\n    require(host_conversation_context()[\'conversation_url\'] == url, \'SYNTHETIC_CONTEXT_REQUIRED\')\n    before = {item[\'path\']: digest(item[\'path\']) for item in report[\'files\']}\n    # Build itself performed the fixed-template checks. Do not invent a human\n    # or model image review in this isolated qualification.\n    daily.validate_result(report, project)\n    accepted = report\n    require(accepted[\'status\'] == \'REPORT_ACCEPTED\'\n            and accepted[\'acceptance_basis\'] == daily.PROGRAMMATIC_BASIS\n            and accepted[\'programmatic_checks_passed\'] is True\n            and accepted[\'visual_reviewed\'] is False\n            and accepted[\'visual_review_required\'] is False\n            and accepted[\'next_action\'] == \'publish_files_then_reply_once\'\n            and accepted[\'model_publication_receipt_transcription_required\'] is False\n            and accepted[\'final_text_command_required\'] is False\n            and \'after_publication_command\' not in accepted\n            and \'publication_projection_file\' not in accepted,\n            \'SINGLE_PUBLICATION_HANDOFF_NOT_ACCEPTED\')\n    text_path = Path(accepted[\'reply_text_file\'])\n    require(accepted[\'final_text_file\'] == str(text_path), \'ACCEPTED_TXT_IDENTITY\')\n    text = text_path.read_text(encoding=\'utf-8\')\n    data = daily.snapshot(project)\n    require(text == daily.reply_text(data).rstrip() + \'\\n\\n[点击查看完整日报](\' + url + \')\\n\'\n            and text.count(url) == 1 and accepted[\'reply_text_ready\'] is True\n            and accepted[\'ready_after_successful_publication\'] is True\n            and accepted[\'external_delivery_ready\'] is False\n            and accepted[\'delivery_requires_successful_publication\'] is True,\n            \'SINGLE_LINKED_TEXT_NOT_READY\')\n    require(not (project / \'delivery\').exists(), \'NORMAL_FLOW_CREATED_EXTRA_DELIVERY\')\n    image = next(Path(item[\'path\']) for item in accepted[\'files\'] if item[\'path\'].endswith(\'.png\'))\n    hidden = image.with_name(\'.synthetic-withheld.png\')\n    image.rename(hidden)\n    try:\n        try:\n            daily.validate_result(accepted, project)\n        except daily.Failure as exc:\n            require(exc.code == \'OUTPUT_CHANGED\', \'MISSING_ARTIFACT_ERROR_CHANGED\')\n        else:\n            raise ValueError(\'MISSING_ARTIFACT_WAS_ACCEPTED\')\n    finally:\n        hidden.rename(image)\n    # A legacy call without current env must not create or overwrite a draft,\n    # even when accepted files already contain a previously valid URL.\n    previous = os.environ.pop(CONVERSATION_ENV)\n    try:\n        missing_context = daily.final_text(SimpleNamespace(workspace=str(project), publication_receipt=None))\n        require(missing_context[\'status\'] == \'WAITING_FOR_CONVERSATION_LINK\'\n                and missing_context[\'reply_text_ready\'] is False\n                and missing_context[\'external_delivery_ready\'] is False\n                and missing_context[\'final_text_file\'] is None, \'MISSING_CURRENT_CONTEXT_NOT_BLOCKED\')\n    finally:\n        os.environ[CONVERSATION_ENV] = previous\n    require(not (project / \'delivery\').exists(), \'MISSING_CONTEXT_CREATED_DRAFT\')\n    require(before == {item[\'path\']: digest(item[\'path\']) for item in accepted[\'files\']}, \'ACCEPTED_ARTIFACTS_CHANGED\')\n    fallback_project = make_freight_project(daily, project, temporary, \'freight-compose\')\n    fallback = daily.snapshot(fallback_project)\n    assert_freight_dates(daily, fallback, daily.load(project / \'market.json\')[\'freight\'])\n    return {\n        \'status\': \'SINGLE_LINKED_TEXT_AND_MISSING_CONTEXT_QUALIFIED\',\n        \'synthetic_host_environment_used\': True,\n        \'real_host_context_injection_proven\': False,\n        \'real_publication_proven\': False,\n        \'complete_artifacts_accepted\': True,\n        \'programmatic_acceptance_verified\': True,\n        \'model_visual_review_used\': False,\n        \'ocr_used\': False,\n        \'missing_artifact_rejected\': True,\n        \'missing_host_context_blocked\': True,\n        \'accepted_four_files_unchanged\': True,\n        \'single_text_ready_before_publication\': True,\n        \'publication_transcription_required\': False,\n        \'final_text_command_required\': False,\n        \'freight_fallback_date_marked\': True,\n        \'messages_sent\': 0,\n        \'network_requests\': 0,\n        \'final_text_sha256\': digest(text_path),\n    }\n\n\ndef make_freight_project(daily, project, temporary, name):\n    """Copy only synthetic prepared inputs, then record dated missing/older quotes."""\n    project, temporary = Path(project), Path(temporary)\n    require(project.is_relative_to(temporary), \'SYNTHETIC_PROJECT_REQUIRED\')\n    target_project = temporary / name\n    require(not target_project.exists(), \'SYNTHETIC_PROJECT_EXISTS\')\n    target_project.mkdir()\n    for filename in (\'market.json\', \'financial.json\', \'research.json\', \'news.json\', \'PROJECT.json\'):\n        shutil.copyfile(project / filename, target_project / filename)\n    shutil.copytree(project / \'sources\', target_project / \'sources\')\n    state = daily.load(target_project / \'PROJECT.json\')\n    original = daily.load(target_project / \'market.json\')\n    target = dt.date.fromisoformat(state[\'data_date\'])\n    records = []\n    for kind in (\'oil\', \'gas\', \'freight\'):\n        for index, quote in enumerate(original[kind]):\n            current = dict(code=quote[\'code\'], date=target.isoformat(), value=quote[\'value\'], status=\'available\',\n                           unit=quote[\'unit\'], item=quote.get(\'contract\', quote[\'name\']), raw_close=str(quote[\'value\']))\n            previous = dict(current, date=(target - dt.timedelta(days=4)).isoformat(), value=100.0, raw_close=\'100\')\n            records.append(previous)\n            if kind == \'freight\':\n                records.append(dict(current, date=(target - dt.timedelta(days=1 + index % 2)).isoformat()))\n                current.update(value=None, status=\'missing\', raw_close=\'\')\n            records.append(current)\n    energy = daily.market.build(daily.market.previous_months(target).isoformat(), target.isoformat(), records, {})\n    daily.save(target_project / \'market.json\', energy)\n    state[\'version\'] = daily.VERSION\n    state[\'input_hashes\'][\'market\'] = digest(target_project / \'market.json\')\n    daily.save(target_project / \'PROJECT.json\', state)\n    return target_project\n\n\ndef assert_freight_dates(daily, data, original_quotes, rendered_html=None, rendered_text=None):\n    from delivery_text import compose\n    require(len(data[\'freight\']) == 8 and all(q[\'status\'] == \'latest_available\' and q[\'date\'] < data[\'data_date\']\n            and q[\'target_date\'] == data[\'data_date\'] for q in data[\'freight\']), \'FREIGHT_FALLBACK_NOT_APPLIED\')\n    html = daily.render(data) if rendered_html is None else rendered_html\n    text = daily.reply_text(data) if rendered_text is None else rendered_text\n    final = compose(data, text, {\'confirmed\': False, \'conversation_url\': None})\n    table = re.search(r\'<table class="freight-table">(.*?)</table>\', html, re.S)\n    require(table is not None, \'FREIGHT_TABLE_MISSING\')\n    cells = re.findall(r\'<td>(.*?)</td>\', table.group(1), re.S)\n    require(len(cells) == len(original_quotes) == 8, \'FREIGHT_CELL_COUNT\')\n    target = dt.date.fromisoformat(data[\'data_date\'])\n    for index, (quote, original, cell) in enumerate(zip(data[\'freight\'], original_quotes, cells)):\n        expected_date = (target - dt.timedelta(days=1 + index % 2)).isoformat()\n        require(quote[\'code\'] == original[\'code\'] and quote[\'value\'] == original[\'value\']\n                and quote[\'date\'] == expected_date, \'FREIGHT_REFERENCE_MAPPING_CHANGED\')\n        value = format(quote[\'value\'], \',.\' + str(quote.get(\'decimals\', 2)) + \'f\')\n        require(\'<b>\' + value + \'</b>\' in cell and expected_date in html_module.unescape(cell),\n                \'FREIGHT_CELL_DATE_OR_VALUE_MISMATCH\')\n        quote_line = \'%s：%s %s\' % (quote[\'name\'], value, quote[\'unit\'])\n        following = text.split(quote_line, 1)\n        require(len(following) == 2 and expected_date in following[1].splitlines()[1],\n                \'FREIGHT_TEXT_DATE_MAPPING_CHANGED\')\n        require(quote[\'date\'] in final,\n                \'FREIGHT_DATE_MISSING_FROM_DELIVERABLE\')\n    require(\'最近已取得报价\' in html and \'最近已取得报价\' in text and data[\'quality\'] == \'数据不完整\',\n            \'FREIGHT_FALLBACK_MISREPRESENTED_AS_TARGET\')\n\n\ndef qualify_freight_render(daily, project, temporary):\n    """A second real render in the deployment\'s UID1000 public mount."""\n    target = make_freight_project(daily, project, temporary, \'freight-render\')\n    data = daily.snapshot(target)\n    original_quotes = daily.load(Path(project) / \'market.json\')[\'freight\']\n    assert_freight_dates(daily, data, original_quotes)\n    report = daily.build(SimpleNamespace(workspace=str(target)))\n    daily.validate_result(report, target)\n    require(report[\'status\'] == \'REPORT_ACCEPTED\'\n            and report[\'acceptance_basis\'] == daily.PROGRAMMATIC_BASIS\n            and report[\'programmatic_checks_passed\'] is True\n            and report[\'visual_reviewed\'] is False\n            and report[\'png\'][\'programmatic_checks\'][\'global_warning_hidden_in_html_and_png\'] is True,\n            \'FREIGHT_PROGRAMMATIC_ACCEPTANCE_REQUIRED\')\n    paths = {Path(item[\'path\']).suffix: Path(item[\'path\']) for item in report[\'files\']}\n    actual = daily.load(paths[\'.json\'])\n    require(actual[\'freight\'] == data[\'freight\'], \'PERSISTED_FREIGHT_DATA_CHANGED\')\n    assert_freight_dates(daily, actual, original_quotes, paths[\'.html\'].read_text(encoding=\'utf-8\'),\n                        paths[\'.txt\'].read_text(encoding=\'utf-8\'))\n    require(\'<aside hidden style="display:none;\' in paths[\'.html\'].read_text(encoding=\'utf-8\')\n            and actual[\'quality\'] == \'数据不完整\'\n            and \'数据不完整\' in paths[\'.txt\'].read_text(encoding=\'utf-8\'),\n            \'FREIGHT_FACTS_AND_HTML_DISPLAY_REQUIRED\')\n    image = next(item for item in report[\'files\'] if item[\'path\'].endswith(\'.png\'))\n    with paths[\'.png\'].open(\'rb\') as stream:\n        header = stream.read(24)\n    require(header[:8] == b\'\\x89PNG\\r\\n\\x1a\\n\' and len(header) == 24\n            and struct.unpack(\'>II\', header[16:24]) == (780, report[\'png\'][\'height\']), \'FREIGHT_PNG_HEADER_INVALID\')\n    require(image[\'bytes\'] > 1000 and report[\'png\'][\'width\'] == 780 and digest(image[\'path\']) == image[\'sha256\'],\n            \'FREIGHT_FALLBACK_PNG_INVALID\')\n    return {\'status\': \'FREIGHT_FALLBACK_RENDER_QUALIFIED\', \'actual_dates_verified\': True,\n            \'programmatic_acceptance_verified\': True, \'model_visual_review_used\': False,\n            \'global_warning_hidden_in_html_and_png\': True, \'non_png_quality_facts_preserved\': True,\n            \'png_sha256\': image[\'sha256\'], \'png_bytes\': image[\'bytes\'], \'png\': report[\'png\'],\n            \'render_seconds\': report[\'render_seconds\'], \'provider_requests\': 0,\n            \'visual_business_acceptance_pending\': True}\n'
FINAL_TEXT_PROBE_SHA = 'cdaa492e06f6500e3efc322bd5f1300adeb2c637a6d253c2839fbbc2551cefa2'
FIXTURES = {'test_energy_market_daily.py': 'f026268b7a2e501cb3f6d09bbda4c5405780342cb3eba8816b4ab4fe769de1b9', 'test_energy_html_delivery_20261114.py': '99faf850a1d7d99bd695708b0240057a2d2760dcffaaeabd3b05f5d3231223c8', 'test_energy_html_news_stage_20261114.py': 'f703d89f0d06b537b321339996bf37424514824a0add3a4af55abfdec402fee7', 'fixtures/approved.json': '3edab45e9afa77e78332bc41a8474dcb61d233b0fc047333fed44d496d9c47df', 'fixtures/original_renderer.py': 'f361492a588d481d6b50ed7740fbf839b3c2775a389bb2f43ceaeebb984f15eb', 'fixtures/dxy-source.txt': 'efccb90d7d550aa27298de6d9fea8a1ebfbda7555d68a609b32a73f81c7f8aff', 'fixtures/cny-source.txt': '68d3e43b268470f9395268d265b86f823e3cd24cb96d28239c91d9a2587621fa', 'published-2.1.2/scripts/common.py': '3ba16e41756c188d8c67ae97ad03b1b5878e3c48ecd7221aeabcd9e366ac8cb0', 'published-2.1.2/scripts/market.py': '0ade49587ad07af0da852e384090525466b79bd936cd9edaf21789493c8a115c', 'published-2.1.2/scripts/research.py': '0652d2f28854e570ef5b22e8d079f7b1d68d12d217475fee5e28d379cc18a0ee', 'published-2.1.2/scripts/report_renderer.py': '2d82ef0d5a81ff5fd21f5695fe94f8cfca7de3df2ea47911127d7c9346b64c8f', 'published-2.1.2/scripts/browser_runtime.py': '9844b8e4c5e6a6de99a6f7d9cf42f2aafeda1dea5e2b9966f709da7cebf71ae8', 'published-2.1.2/scripts/daily.py': '5e2e6beccff0a2cbdad2bba998dc1b1c40732e5de3a1a40b0c804aeff68852a5'}
OFFLINE_SUITES = {'test_energy_html_delivery_20261114.py': ['ProgrammaticBuildTests', 'BrowserPipelineTests'], 'test_energy_html_news_stage_20261114.py': ['NewsStageReceiptTests']}
OFFLINE_TEST_COUNT = 27
SYNTHETIC_CONVERSATION_URL = 'https://example.invalid/workspace/chats/11111111-1111-4111-8111-111111111111'


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def no_links(path):
    for part in (path,) + tuple(path.parents):
        need(not part.is_symlink(), 'READONLY_SOURCE_LINK')


def stamp(state):
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


def read(path, limit=1048576):
    no_links(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'READONLY_SOURCE_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'READONLY_SOURCE_CHANGED')
        return raw
    finally:
        os.close(fd)


def inventory(root):
    no_links(root)
    rows, total = [], 0
    for directory, folders, names in os.walk(str(root), followlinks=False):
        for name in folders:
            path = Path(directory) / name
            no_links(path)
            need(path.is_dir(), 'READONLY_DIRECTORY_TYPE')
        for name in names:
            path = Path(directory) / name
            raw = read(path)
            total += len(raw)
            rows.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)})
            need(len(rows) <= 128 and total <= 32 * 1024 * 1024, 'READONLY_SKILL_TREE_BOUND')
    return sorted(rows, key=lambda row: row['path'])


def verify_skill(expected, root=SKILL):
    rows = inventory(root)
    need(len(rows) == CANDIDATE_FILES and sha(canonical(rows)) == expected, 'STAGED_SKILL_TREE_CHANGED')
    manifest = json.loads(read(root / 'MANIFEST.json'))
    need(manifest.get('schema') == 'energy-daily-portable-files.v1' and manifest.get('version') == CANDIDATE_VERSION, 'STAGED_MANIFEST_IDENTITY')
    files = manifest.get('files')
    need(isinstance(files, dict) and len(files) == CANDIDATE_FILES - 1, 'STAGED_MANIFEST_COUNT')
    actual = {row['path']: {key: row[key] for key in ('bytes', 'sha256')} for row in rows if row['path'] != 'MANIFEST.json'}
    need(files == actual and 'assets/browser-runtime.json' in files, 'STAGED_MANIFEST_CONTENT_CHANGED')
    return rows


def verify_runtime_manifest(expected, root=RUNTIME):
    raw = read(root / 'RUNTIME.json', 262144)
    need(sha(raw) == expected, 'STAGED_RUNTIME_MANIFEST_CHANGED')
    value = json.loads(raw)
    need(value.get('schema') == 'energy-browser-runtime.v1' and value.get('architecture') == 'linux/arm64'
         and value.get('source_image_id') == 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8', 'STAGED_RUNTIME_IDENTITY')
    return raw


def write_fixtures(files, root):
    need(isinstance(files, dict) and set(FIXTURES).issubset(files) and len(files) <= 64, 'FIXTURE_SET_REQUIRED')
    for name, expected in FIXTURES.items():
        raw = base64.b64decode(files[name], validate=True)
        need(len(raw) <= 1048576 and sha(raw) == expected, 'FIXTURE_CHANGED')
        target = root / 'tests' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def qualify_final_text(daily, project, report, temporary):
    need(FINAL_TEXT_PROBE and sha(FINAL_TEXT_PROBE.encode('utf-8')) == FINAL_TEXT_PROBE_SHA, 'FINAL_TEXT_PROBE_NOT_SEALED')
    active = [True]
    def no_external_calls(event, args):
        if active[0] and (event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'sqlite3.connect')):
            raise Refused('COMPOSE_ONLY_EXTERNAL_OPERATION_FORBIDDEN')
    sys.addaudithook(no_external_calls)
    try:
        scope = {'__name__': 'sealed_compose_only_qualification'}
        exec(compile(FINAL_TEXT_PROBE, '<sealed-compose-only-qualification>', 'exec'), scope)
        value = scope['qualify'](daily, project, report, temporary)
        need(isinstance(value, dict) and value.get('complete_artifacts_accepted') is True
             and value.get('missing_artifact_rejected') is True and value.get('messages_sent') == 0
             and value.get('single_text_ready_before_publication') is True
             and value.get('programmatic_acceptance_verified') is True
             and value.get('model_visual_review_used') is False
             and value.get('ocr_used') is False
             and value.get('missing_host_context_blocked') is True
             and value.get('synthetic_host_environment_used') is True
             and value.get('real_host_context_injection_proven') is False
             and value.get('freight_fallback_date_marked') is True
             and value.get('network_requests') == 0, 'COMPOSE_ONLY_CASES_NOT_ACCEPTED')
        return value
    finally:
        active[0] = False


def offline_regressions(temporary):
    """Run only the sealed reviewed suites against this exact mounted candidate."""
    need(isinstance(OFFLINE_SUITES,dict) and OFFLINE_SUITES and OFFLINE_TEST_COUNT > 0,
         'OFFLINE_SUITES_NOT_SEALED')
    suite = unittest.TestSuite()
    for filename, names in OFFLINE_SUITES.items():
        path = temporary / 'tests' / filename
        source = read(path).decode('utf-8')
        marker = "SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.4'"
        need(source.count(marker)==1,'REGRESSION_CANDIDATE_MARKER')
        source = source.replace(marker,'SKILL = Path(' + repr(str(SKILL)) + ')')
        # Tests of old accept receipts load the immutable published source. Its
        # relocation is explicit; current Skill code always comes from SKILL.
        original = "SKILL.parent / 'energy-market-daily-2.1.2/scripts'"
        previous = "SKILL.parent / 'energy-market-daily-2.1.2/scripts/daily.py'"
        if filename == 'test_energy_html_delivery_20261114.py':
            need(source.count(original)==1 and source.count(previous)==1,
                 'LEGACY_SOURCE_FIXTURE_MARKER')
            fixed = temporary / 'tests/published-2.1.2/scripts'
            source = source.replace(original,'Path(' + repr(str(fixed)) + ')')
            source = source.replace(previous,'Path(' + repr(str(fixed / 'daily.py')) + ')')
        scope = {'__name__':'deployment_programmatic_tests_'+path.stem,'__file__':str(path)}
        exec(compile(source,str(path),'exec'),scope)
        need(isinstance(names,list) and names,'OFFLINE_SUITE_CLASS_SET')
        for name in names:
            need(isinstance(scope.get(name),type) and issubclass(scope[name],unittest.TestCase),
                 'OFFLINE_SUITE_CLASS_CHANGED')
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(scope[name]))
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log,verbosity=1).run(suite)
    summary = {'run':result.testsRun,'errors':len(result.errors),'failures':len(result.failures),
               'skipped':len(result.skipped),'passed':result.wasSuccessful(),
               'diagnostic_sha256':sha(log.getvalue().encode()),'candidate_import_bound':True}
    need(result.testsRun==OFFLINE_TEST_COUNT and result.wasSuccessful() and not result.skipped,'OFFLINE_REGRESSIONS_FAILED')
    return summary


def qualify_freight_render(daily, project, temporary):
    need(FINAL_TEXT_PROBE and sha(FINAL_TEXT_PROBE.encode('utf-8')) == FINAL_TEXT_PROBE_SHA, 'FINAL_TEXT_PROBE_NOT_SEALED')
    scope = {'__name__': 'sealed_freight_render_qualification'}
    exec(compile(FINAL_TEXT_PROBE, '<sealed-freight-render-qualification>', 'exec'), scope)
    value = scope['qualify_freight_render'](daily, project, temporary)
    need(isinstance(value, dict) and value.get('status') == 'FREIGHT_FALLBACK_RENDER_QUALIFIED'
         and value.get('actual_dates_verified') is True
         and value.get('programmatic_acceptance_verified') is True
         and value.get('model_visual_review_used') is False
         and value.get('global_warning_hidden_in_html_and_png') is True
         and value.get('non_png_quality_facts_preserved') is True
         and isinstance(value.get('png_sha256'), str) and re.fullmatch('[0-9a-f]{64}', value['png_sha256'])
         and type(value.get('png_bytes')) is int and value['png_bytes'] > 1000
         and isinstance(value.get('png'), dict) and value['png'].get('width') == 780
         and type(value['png'].get('height')) is int and value['png']['height'] > 0
         and value['png'].get('browser_fallback_count') == 0, 'FREIGHT_RENDER_CASES_NOT_ACCEPTED')
    return value


def main():
    out = {'schema': 'energy-programmatic-skill-verification-child.v1', 'status': 'INCOMPLETE',
           'provider_requests': 0, 'credentials_read': False, 'credentials_exported': False,
           'shared_skill_modified': False, 'work_messages_sent': 0, 'temporary_project_cleaned': False}
    stage, temporary = 'identity', None
    try:
        sys.dont_write_bytecode = True
        need(os.geteuid() == 1000 and os.getegid() == 1000 and platform.machine() in ('aarch64', 'arm64'), 'FIXED_ARM64_NONROOT_IDENTITY_REQUIRED')
        out.update(uid=os.geteuid(), gid=os.getegid())
        raw = sys.stdin.buffer.read(8388609)
        need(len(raw) <= 8388608, 'PAYLOAD_BOUND')
        payload = json.loads(raw)
        need(set(payload) == {'package_sha256', 'after_tree_sha256', 'runtime_manifest_sha256', 'png_sha256', 'test_files'}
             and payload['package_sha256'] == PACKAGE_SHA
             and all(isinstance(payload[key], str) and re.fullmatch('[0-9a-f]{64}', payload[key])
                     for key in ('after_tree_sha256', 'runtime_manifest_sha256', 'png_sha256')), 'PUBLICATION_PAYLOAD_INVALID')
        need(CANDIDATE_VERSION == '2.1.4-portable' and CANDIDATE_FILES > 107 and FINAL_TEXT_PROBE
             and sha(FINAL_TEXT_PROBE.encode('utf-8')) == FINAL_TEXT_PROBE_SHA, 'FINAL_TEXT_CANDIDATE_NOT_SEALED')
        stage = 'read_staged_shared_skill_and_runtime'
        before = verify_skill(payload['after_tree_sha256'])
        runtime_before = verify_runtime_manifest(payload['runtime_manifest_sha256'])
        binding = json.loads(read(SKILL / 'assets/browser-runtime.json'))
        need(binding == {'schema': 'energy-browser-runtime-binding.v1', 'directory': '../.energy-browser-runtime-20261112-r1/linux-arm64',
                         'manifest_sha256': payload['runtime_manifest_sha256']}, 'STAGED_BINDING_CHANGED')
        with tempfile.TemporaryDirectory(prefix='energy-publish-check-', dir='/tmp') as location:
            temporary = Path(location)
            home, work = temporary / 'home', temporary / 'tmp'
            home.mkdir(); work.mkdir()
            os.environ.clear()
            os.environ.update(HOME=str(home), TMPDIR=str(work), PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8',
                              PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
                              DEERFLOW_CONVERSATION_URL=SYNTHETIC_CONVERSATION_URL)
            out.update(synthetic_host_environment_used=True,real_host_context_injection_proven=False,
                       real_publication_proven=False)
            tempfile.tempdir = str(work)
            write_fixtures(payload['test_files'], temporary)
            sys.path[:0] = [str(SKILL / 'scripts'), str(temporary / 'tests')]
            import test_energy_market_daily as fixtures
            daily = importlib.import_module('daily')
            need(Path(daily.__file__).resolve() == (SKILL / 'scripts/daily.py').resolve() and daily.VERSION == CANDIDATE_VERSION, 'MOUNTED_SKILL_IMPORT_REQUIRED')
            stage = 'sealed_offline_regressions_as_uid1000'
            out['offline_tests'] = offline_regressions(temporary)
            project = temporary / 'project'
            project.mkdir()
            fixtures.project_fixture(project)
            source_files = ('market.json', 'financial.json', 'research.json', 'news.json')
            inputs = {name: sha(read(project / name)) for name in source_files}
            selected, original_runtime = [], daily.runtime_browser
            @contextlib.contextmanager
            def observed_runtime():
                with original_runtime() as executable:
                    need(isinstance(executable, str) and Path(executable).is_file() and os.access(executable, os.X_OK)
                         and not Path(executable).is_relative_to(RUNTIME), 'LOCAL_RUNTIME_COPY_REQUIRED')
                    selected.append(executable)
                    yield executable
            stage = 'build_from_readonly_shared_mounts_as_uid1000'
            daily.runtime_browser = observed_runtime
            try:
                # Keep a real fileno for Playwright's driver stderr transport.
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    report = daily.build(type('Args', (), {'workspace': str(project)})())
            finally:
                daily.runtime_browser = original_runtime
            need(len(selected) == 1 and not Path(selected[0]).exists() and report['png'].get('browser_fallback_count') == 0, 'MANAGED_RUNTIME_COPY_OR_CLEANUP_FAILED')
            daily.validate_result(report, project)
            need(report.get('status') == 'REPORT_ACCEPTED'
                 and report.get('acceptance_basis') == daily.PROGRAMMATIC_BASIS
                 and report.get('programmatic_checks_passed') is True
                 and report.get('visual_reviewed') is False
                 and report.get('visual_review_required') is False
                 and report.get('png',{}).get('programmatic_checks',{}).get('global_warning_hidden_in_html_and_png') is False,
                 'PROGRAMMATIC_FIXED_REPORT_NOT_ACCEPTED')
            image = next(item for item in report['files'] if item['path'].endswith('.png'))
            png = read(Path(image['path']), 3145728)
            actual_png = sha(png)
            need(actual_png == image['sha256'] and len(png) == image['bytes'], 'GENERATED_PNG_RECEIPT_CHANGED')
            need(inputs == {name: sha(read(project / name)) for name in source_files}, 'FIXED_SOURCES_CHANGED')
            out.update(package_sha256=PACKAGE_SHA, after_tree_sha256=payload['after_tree_sha256'],
                       runtime_manifest_sha256=payload['runtime_manifest_sha256'], png_sha256=actual_png,
                       render_seconds=report['render_seconds'], png=report['png'],
                       acceptance_basis=report['acceptance_basis'],programmatic_checks_passed=True,
                       visual_reviewed=False,visual_review_required=False,
                       automatic_binding_used=True, private_local_copy_cleaned=True, synthetic_sources_unchanged=True)
            if actual_png != payload['png_sha256']:
                out.update(expected_png_sha256=payload['png_sha256'], sample_base64=base64.b64encode(png).decode('ascii'), visual_review_pending=True)
                raise Refused('PUBLISHED_PNG_DIFFERS_FROM_QUALIFIED')
            stage = 'single_text_complete_and_missing_context_cases'
            out['final_text'] = qualify_final_text(daily, project, report, temporary)
            stage = 'freight_fallback_dated_report_as_uid1000'
            selected.clear()
            daily.runtime_browser = observed_runtime
            try:
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    out['freight_render'] = qualify_freight_render(daily, project, temporary)
            finally:
                daily.runtime_browser = original_runtime
            need(len(selected) == 1 and not Path(selected[0]).exists(), 'FREIGHT_RUNTIME_COPY_OR_CLEANUP_FAILED')
        stage = 'stable_shared_source_readback'
        need(before == verify_skill(payload['after_tree_sha256']) and runtime_before == verify_runtime_manifest(payload['runtime_manifest_sha256']), 'READONLY_SOURCES_CHANGED_DURING_BUILD')
        out['status'] = 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED'
    except Exception as exc:
        out.update(last_step=stage, error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'PUBLICATION_CHECK_FAILED_REDACTED')
    finally:
        out['temporary_project_cleaned'] = temporary is not None and not temporary.exists()
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
