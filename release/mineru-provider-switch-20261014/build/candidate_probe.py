"""No pytest installation, no provider calls; runs with the actual image Python."""
import importlib.util
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

BASE = 'https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02'
QUERY = BASE + '/dynamic/member1/mineru-for-zy-agent/tasks'
app = None


class Response:
    def __init__(self, status, data):
        self.status_code, self.data, self.text = status, data, 'synthetic'

    def json(self):
        return self.data


class Session:
    def __init__(self, submission=None, statuses=None, results=None):
        self.calls = []
        self.submission = submission if submission is not None else Response(202, {
            'task_id': 'task-1', 'status_url': 'https://untrusted.invalid/tasks/other'})
        self.statuses = statuses or [Response(200, {'status': 'completed'})]
        self.results = results or [Response(200, {'results': {'document': {'md_content': 'synthetic', 'images': {}}}})]

    def call(self, method, url, value, kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, url, **kwargs):
        return self.call('POST', url, self.submission, kwargs)

    def get(self, url, **kwargs):
        return self.call('GET', url, (self.results if url.endswith('/result') else self.statuses).pop(0), kwargs)


def client(session, **kw):
    return app.EnterpriseMinerUClient(api_url=BASE, api_token='synthetic-not-a-credential',
        request_mode='async', timeout_seconds=30, poll_interval_seconds=0.001,
        transient_poll_retries=2, session=session, **kw)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.sleep = patch.object(app.time, 'sleep', lambda seconds: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.env.stop()

    def test_new_async_paths_one_submission(self):
        os.environ['MINERU_TASK_QUERY_URL'] = QUERY
        s = Session()
        result = client(s).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
        self.assertEqual(result['results']['document']['md_content'], 'synthetic')
        self.assertEqual([(m, u) for m, u, _ in s.calls], [('POST', BASE+'/tasks'),
            ('GET', QUERY+'/task-1'), ('GET', QUERY+'/task-1/result')])
        self.assertTrue(all(k['allow_redirects'] is False for _, _, k in s.calls))
        self.assertEqual(s.calls[0][2]['data']['return_images'], 'true')

    def test_legacy_config_unchanged(self):
        for value in (None, '', '   '):
            with self.subTest(value=value):
                s = Session()
                client(s, task_query_url=value).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
                self.assertEqual(s.calls[1][1], BASE+'/tasks/task-1')

    def test_image_mime_and_conversion(self):
        s = Session()
        client(s, task_query_url=QUERY).parse_image(b'synthetic', 'image.png', '.png')
        self.assertEqual(s.calls[0][2]['files']['files'][2], 'image/png')
        result = app.weknora_response('synthetic', {})
        self.assertIsInstance(result, dict)
        self.assertIn('synthetic', json.dumps(result))

    def test_invalid_queries_fail_before_network(self):
        for url in ('https://outside.invalid/tasks', 'http://aiapi.cnooc.com.cn/tasks',
                    'https://aiapi.cnooc.com.cn:444/tasks', 'https://user:secret@aiapi.cnooc.com.cn/tasks',
                    QUERY+'?token=x', QUERY+'#x', BASE+'/../tasks', BASE+'/%2e%2e/tasks',
                    BASE+'//tasks', BASE+'/./tasks', BASE+'/tasks/{task_id}', '/tasks',
                    BASE+'/space here/tasks', BASE+'/\\x/tasks', BASE+'/\nx/tasks'):
            with self.subTest(case=url):
                s = Session()
                with self.assertRaises(ValueError):
                    client(s, task_query_url=url)
                self.assertEqual(s.calls, [])

    def test_redirects_rejected_all_phases(self):
        for stage in ('submit', 'status', 'result'):
            for status in (301, 302, 307, 308):
                with self.subTest(stage=stage, status=status):
                    r = Response(status, {'task_id': 'task-1', 'status': 'completed'})
                    s = Session(**({'submission': r} if stage == 'submit' else
                        {'statuses': [r]} if stage == 'status' else {'results': [r]}))
                    with self.assertRaisesRegex(RuntimeError, 'redirect'):
                        client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
                    self.assertEqual(sum(m == 'POST' for m, _, _ in s.calls), 1)
                    self.assertEqual(len(s.calls), {'submit': 1, 'status': 2, 'result': 3}[stage])

    def test_uncertain_submit_never_retried(self):
        s = Session(submission=app.requests.ReadTimeout('synthetic'))
        with self.assertRaises(app.requests.ReadTimeout):
            client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
        self.assertEqual(len(s.calls), 1)

    def test_poll_recovers_only_same_task(self):
        s = Session(statuses=[app.requests.ReadTimeout('synthetic'), Response(404, {}),
            Response(200, {'status': 'completed'})])
        client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
        self.assertEqual(sum(m == 'POST' for m, _, _ in s.calls), 1)
        self.assertEqual({u for m, u, _ in s.calls if m == 'GET'}, {QUERY+'/task-1', QUERY+'/task-1/result'})

    def test_poll_deadline_not_reset_by_submit(self):
        clock, s = [0], Session()
        previous = s.post
        def delayed(*args, **kwargs):
            result = previous(*args, **kwargs)
            clock[0] = 31
            return result
        s.post = delayed
        with patch.object(app.time, 'monotonic', lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
        self.assertEqual(len(s.calls), 1)

    def test_poll_retry_exhaustion(self):
        s = Session(statuses=[app.requests.ReadTimeout('synthetic') for _ in range(3)])
        with self.assertRaises(app.requests.ReadTimeout):
            client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
        self.assertEqual(len(s.calls), 4)

    def test_untrusted_task_id_rejected(self):
        for value in ('.', '..', '../secret', 'a/b', 'a?x', '%2e%2e', ''):
            with self.subTest(value=value):
                s = Session(submission=Response(202, {'task_id': value}))
                with self.assertRaises(RuntimeError):
                    client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
                self.assertEqual(len(s.calls), 1)

    def test_terminal_failure_does_not_fetch_result(self):
        for status in ('failed', 'error'):
            s = Session(statuses=[Response(200, {'status': status})])
            with self.assertRaises(RuntimeError):
                client(s, task_query_url=QUERY).parse_pdf(b'%PDF-synthetic', 'sample.pdf')
            self.assertEqual(len(s.calls), 2)

    def test_real_requests_session_keeps_tls_and_no_proxy(self):
        c = app.EnterpriseMinerUClient(api_url=BASE, api_token='synthetic', task_query_url=QUERY)
        self.assertIs(c.session.trust_env, False)
        self.assertIs(c.session.verify, True)
        c.session.close()

    def test_native_parse_serialization_and_health_snapshot(self):
        coord = app.ParseCoordinator()
        entered, release = threading.Event(), threading.Event()
        def hold():
            entered.set()
            if not release.wait(3):
                raise RuntimeError('fixture_wait_timeout')
            return {}
        t = threading.Thread(target=lambda: coord.run(hold, filename='synthetic.pdf', content_bytes=1))
        t.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(coord.snapshot()['active'], 1)
            with self.assertRaises(app.ParseWorkerBusy):
                coord.run(lambda: {}, filename='other.pdf', content_bytes=1)
        finally:
            release.set()
            t.join(4)
        self.assertFalse(t.is_alive())
        self.assertEqual(coord.snapshot()['active'], 0)
        self.assertEqual(coord.run(lambda: {'ok': True}, filename='next.pdf', content_bytes=1), {'ok': True})


def main():
    global app
    source = Path(sys.argv[1]) if len(sys.argv) == 2 else Path('/app/deerflow_mineru_adapter.py')
    # Whole daemon/executor has no external network; this is a second fixture-level guard.
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network_forbidden')):
        spec = importlib.util.spec_from_file_location('mineru_candidate_under_test', str(source))
        app = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = app
        spec.loader.exec_module(app)
        logging.disable(logging.CRITICAL)
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(CandidateTests))
    print(json.dumps(dict(mineru_candidate_fixture='PASS' if result.wasSuccessful() else 'FAIL',
        tests=result.testsRun, provider_requests=0, native_parser='SERIALIZED',
        business_document_quality='NOT_TESTED', concurrent_64='NOT_TESTED')))
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
