"""Offline real-CLI delivery composition fixtures; no image/HTTP/model requests."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / 'portable-skills/candidates/energy-market-daily-2.1.0/scripts/daily.py'
APPROVED = REPO / 'portable-skills/tests/fixtures/approved.json'
THREAD = '545097ca-bd47-4416-888e-b6579229c865'
URL = 'https://cnoocclaw.cnooc.com.cn/workspace/chats/' + THREAD


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def fingerprint(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def seed(project, version='2.0.3-portable', missing=False, holiday=False):
    project.mkdir()
    data = json.loads(APPROVED.read_text(encoding='utf-8-sig'))
    data['quality'] = '数据不完整' if missing else '休市标注版' if holiday else '完整报告'
    if missing:
        for item in data['freight']:
            item.update(value=None, change=None, status='missing', missing_reason='供应商未返回目标日有效报价')
    if holiday:
        data['gas'][0].update(value=None, change=None, status='non_trading', missing_reason='供应商明确休市')
    generation = 'generation-' + 'a' * 32
    destination = project / 'outputs' / generation
    destination.mkdir(parents=True)
    stem = '油气与金融市场日报_' + data['publication_date']
    text = ('油气与金融市场日报\n发布日期：' + data['publication_date'] + '；行情日期：' + data['data_date']
            + '\n报告状态：' + data['quality'] + '\n\n固定原文，不重新摘要。\n[新闻原页](https://oilprice.com/test-story.html)\n')
    paths = [destination / (stem + ext) for ext in ('.png', '.html', '.json', '.txt')]
    paths[0].write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 780, 5250) + b'\x00' * 1200)
    paths[1].write_text('<html>synthetic accepted fixture</html>', encoding='utf-8')
    write_json(paths[2], data)
    paths[3].write_text(text, encoding='utf-8')
    result = {'version': version, 'status': 'REPORT_ACCEPTED', 'visual_reviewed': True, 'publication_allowed': True,
              'project': str(project), 'outputs_generation': generation, 'publication_date': data['publication_date'],
              'data_date': data['data_date'], 'quality': data['quality'], 'files': [fingerprint(path) for path in paths],
              'reply_text_file': str(paths[3]), 'next_command': 'python daily.py accept --workspace old'}
    write_json(project / 'PROJECT.json', {'version': version, 'publication_date': data['publication_date']})
    write_json(project / 'RESULT.json', result)
    return result, data, text


def projection(result, artifact=False):
    value = {'name': 'present_files', 'status': 'success', 'tool_call_id': 'call-real-result',
             'content': 'Successfully presented files\nconversation_url: ' + URL,
             'requested_files': [item['path'] for item in result['files']]}
    if artifact:
        value['artifact'] = {'presented_files': ['/mnt/user-data/outputs/' + Path(item['path']).name for item in result['files']],
                             'publication_run_id': 'trusted-runtime-run', 'conversation_url': URL}
    return value


def cli(project, receipt=None, action='final-text', extra=()):
    command = [sys.executable, '-I', '-B', str(ENTRY), action, '--workspace', str(project)]
    if receipt is not None:
        path = project / 'delivery/host-publication.json'
        write_json(path, receipt)
        command += ['--publication-receipt', str(path)]
    output = subprocess.run(command + list(extra), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=False)
    return output.returncode, json.loads(output.stdout.decode('utf-8'))


class FinalTextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name).resolve() / 'project'

    def tearDown(self):
        self.temporary.cleanup()

    def test_visible_receipt_preserves_full_text_and_one_final_file(self):
        result, data, text = seed(self.project)
        original = [fingerprint(Path(item['path'])) for item in result['files']]
        code, output = cli(self.project, projection(result))
        self.assertEqual(code, 0, output); self.assertEqual(output['status'], 'FINAL_TEXT_READY')
        final = Path(output['final_text_file']).read_text(encoding='utf-8')
        self.assertTrue(final.startswith(text)); self.assertIn('PNG长图、HTML、JSON和完整文字已在本次对话发布。', final)
        self.assertTrue(final.endswith('[点击查看完整日报](' + URL + ')\n'))
        self.assertNotIn('附件', final); self.assertEqual(original, [fingerprint(Path(item['path'])) for item in result['files']])
        manifest = json.loads(Path(output['manifest_file']).read_text(encoding='utf-8'))
        self.assertTrue(manifest['caller_supplied_projection_is_not_independent_host_attestation'])
        self.assertTrue(manifest['publication']['publication_run_binding_not_independently_verified'])
        self.assertEqual(manifest['messages_sent'], 0); self.assertEqual(output['messages_sent'], 0)
        before = Path(output['manifest_file']).read_bytes()
        code, repeated = cli(self.project, projection(result)); self.assertEqual(code, 0); self.assertTrue(repeated['reused'])
        self.assertEqual(before, Path(output['manifest_file']).read_bytes())

    def test_complete_artifact_does_not_require_nonexistent_success_field(self):
        result, data, text = seed(self.project)
        receipt = projection(result, artifact=True)
        self.assertNotIn('publication_status', receipt['artifact'])
        code, output = cli(self.project, receipt)
        self.assertEqual(code, 0, output); self.assertTrue(output['publication_confirmed_by_caller_projection'])

    def test_missing_and_holiday_classification_preserved(self):
        result, data, text = seed(self.project, missing=True, holiday=True)
        code, output = cli(self.project, projection(result))
        self.assertEqual(code, 0, output)
        final = Path(output['final_text_file']).read_text(encoding='utf-8')
        self.assertTrue(final.startswith(text)); self.assertIn('航运8项数据未取得', final)
        self.assertIn('天然气1项按已核实来源标注休市', final); self.assertIn('未用旧价冒充目标日行情', final)

    def test_no_receipt_no_link_and_no_success_claim(self):
        result, data, text = seed(self.project)
        code, output = cli(self.project)
        self.assertEqual(code, 0, output); self.assertFalse(output['publication_confirmed_by_caller_projection'])
        final = Path(output['final_text_file']).read_text(encoding='utf-8')
        self.assertTrue(final.startswith(text)); self.assertIn('平台发布尚未确认', final)
        self.assertNotIn('已在本次对话发布', final); self.assertNotIn('点击查看完整日报', final)

    def test_partial_or_failed_publication_never_claims_full_delivery(self):
        result, data, text = seed(self.project)
        for kind in ('partial', 'failed', 'wrong_requested_files', 'hidden_artifact_missing_run'):
            receipt = projection(result, artifact=kind in ('partial', 'hidden_artifact_missing_run'))
            if kind == 'partial': receipt['artifact']['presented_files'].pop()
            elif kind == 'failed': receipt.update(status='error', content='File delivery incomplete')
            elif kind == 'wrong_requested_files': receipt['requested_files'][0] = '/different/' + Path(result['files'][0]['path']).name
            else: receipt['artifact'].pop('publication_run_id')
            code, output = cli(self.project, receipt)
            self.assertEqual(code, 0, (kind, output)); self.assertFalse(output['publication_confirmed_by_caller_projection'])
            final = Path(output['final_text_file']).read_text(encoding='utf-8')
            self.assertIn('平台发布尚未确认', final); self.assertNotIn('点击查看完整日报', final)

    def test_url_shape_and_optional_thread_binding_are_checked(self):
        result, data, text = seed(self.project)
        for bad in ('http://cnoocclaw.cnooc.com.cn/workspace/chats/' + THREAD,
                    URL + '?token=secret', URL + '#fragment', URL.replace('https://', 'https://user:secret@'),
                    'https://cnoocclaw.cnooc.com.cn/drive/home/', URL.replace('cnoocclaw.cnooc.com.cn', '127.0.0.1'),
                    URL.replace('cnoocclaw.cnooc.com.cn', 'localhost')):
            receipt = projection(result); receipt['content'] = 'Successfully presented files\nconversation_url: ' + bad
            code, output = cli(self.project, receipt)
            self.assertEqual(code, 0); self.assertIsNone(output['conversation_url']); self.assertFalse(output['publication_confirmed_by_caller_projection'])
        receipt = projection(result); receipt['thread_id'] = '00000000-0000-0000-0000-000000000000'
        self.assertFalse(cli(self.project, receipt)[1]['publication_confirmed_by_caller_projection'])

    def test_no_domain_is_constructed_from_thread_and_receipt_origin_is_preserved(self):
        result, data, text = seed(self.project)
        receipt = projection(result); supplied = 'https://agents.example.com/workspace/chats/' + THREAD
        receipt['content'] = 'Successfully presented files\nconversation_url: ' + supplied
        code, output = cli(self.project, receipt)
        self.assertEqual(code, 0); self.assertEqual(output['conversation_url'], supplied)
        receipt.pop('content'); receipt['conversation_id'] = THREAD
        self.assertIsNone(cli(self.project, receipt)[1]['conversation_url'])

    def test_unaccepted_or_changed_files_fail_before_delivery_file_creation(self):
        result, data, text = seed(self.project)
        result['status'] = 'REPORT_GENERATED_VISUAL_REVIEW_REQUIRED'; write_json(self.project / 'RESULT.json', result)
        code, output = cli(self.project)
        self.assertEqual(code, 1); self.assertEqual(output['error_code'], 'DELIVERY_REPORT_NOT_ACCEPTED')
        result['status'] = 'REPORT_ACCEPTED'; write_json(self.project / 'RESULT.json', result)
        Path(result['files'][0]['path']).unlink()
        code, output = cli(self.project)
        self.assertEqual(code, 1); self.assertEqual(output['error_code'], 'OUTPUT_CHANGED')
        self.assertFalse((self.project / 'delivery/final.md').exists())

    def test_report_project_or_generation_mismatch_is_rejected(self):
        result, data, text = seed(self.project)
        result['outputs_generation'] = 'generation-' + 'b' * 32; write_json(self.project / 'RESULT.json', result)
        code, output = cli(self.project)
        self.assertEqual(code, 1); self.assertEqual(output['error_code'], 'RESULT_FILE_SET_INVALID')

    def test_old_project_versions_remain_supported(self):
        result, data, text = seed(self.project)
        for version in ('2.0.0-portable', '2.0.1-portable', '2.0.2-portable', '2.0.3-portable', '2.1.0-portable'):
            result['version'] = version; write_json(self.project / 'RESULT.json', result)
            code, output = cli(self.project)
            self.assertEqual(code, 0, (version, output))

    def test_projection_must_be_inside_project(self):
        result, data, text = seed(self.project)
        outside = self.project.parent / 'outside.json'; write_json(outside, projection(result))
        code, output = cli(self.project, extra=('--publication-receipt', str(outside)))
        self.assertEqual(code, 1); self.assertEqual(output['error_code'], 'DELIVERY_PROJECTION_OUTSIDE_PROJECT')

    def test_real_cli_makes_no_network_or_message_request(self):
        result, data, text = seed(self.project)
        path = self.project / 'delivery/host-publication.json'; write_json(path, projection(result))
        program = "import runpy,sys\ndef audit(event,args):\n if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'): raise RuntimeError('NETWORK_OR_CHILD_BLOCKED')\nsys.addaudithook(audit)\nsys.argv=[sys.argv[1]]+sys.argv[2:]\nrunpy.run_path(sys.argv[0],run_name='__main__')"
        output = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(ENTRY), 'final-text', '--workspace', str(self.project), '--publication-receipt', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(output.returncode, 0, output.stderr.decode()); self.assertEqual(json.loads(output.stdout)['messages_sent'], 0)

    def test_accepted_build_reuse_and_accept_handoff_do_not_self_loop(self):
        result, data, text = seed(self.project)
        program = "import sys,json;from pathlib import Path;from types import SimpleNamespace;sys.path.insert(0,str(Path(sys.argv[1]).parent));import daily;p=Path(sys.argv[2]);r=json.loads((p/'RESULT.json').read_text(encoding='utf-8'));data=json.loads(next(Path(i['path']) for i in r['files'] if i['path'].endswith('.json')).read_text(encoding='utf-8'));daily.snapshot=lambda project:data;out=daily.build(SimpleNamespace(workspace=str(p))) if sys.argv[3]=='build' else daily.accept(SimpleNamespace(workspace=str(p),visual_reviewed=True));print(json.dumps(out))"
        for action in ('build', 'accept'):
            output = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(ENTRY), str(self.project), action], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
            self.assertEqual(output.returncode, 0, output.stderr.decode()); value = json.loads(output.stdout)
            self.assertNotIn('next_command', value); self.assertEqual(value['next_action'], 'publish_files_then_final_text')
            self.assertIn('final-text', value['after_publication_command']); self.assertEqual(Path(value['publication_projection_file']), self.project / 'delivery/host-publication.json')


if __name__ == '__main__': unittest.main()
