"""Run a fixed candidate in an isolated maintenance container; emit safe facts."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import unittest
import zipfile


class QualificationRefused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise QualificationRefused(code)


def main():
    out = {'schema': 'energy-portable-arm64-qualification-child.v1', 'status': 'INCOMPLETE',
           'credentials_exported': False, 'skill_published': False, 'work_messages_sent': 0}
    step = 'input'
    try:
        sys.dont_write_bytecode = True
        raw = sys.stdin.buffer.read(2097153)
        need(len(raw) <= 2097152, 'PAYLOAD_BOUND')
        payload = json.loads(raw)
        package = base64.b64decode(payload['package'], validate=True)
        need(hashlib.sha256(package).hexdigest() == payload['package_sha256'], 'PACKAGE_CHANGED')
        root = Path('/tmp/energy-portable-qualification')
        root.mkdir(mode=0o700)
        step = 'extract_public_candidate'
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            need(len(archive.infolist()) <= 256, 'FILE_COUNT_BOUND')
            for info in archive.infolist():
                target = root / info.filename
                need(target.resolve().is_relative_to(root) and not info.is_dir() and info.file_size <= 1048576 and '.private' not in target.parts, 'PACKAGE_LAYOUT')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
        skill = root / 'energy-market-daily'
        manifest = json.loads((skill / 'MANIFEST.json').read_text())
        for relative, item in manifest['files'].items():
            path = skill / relative
            need(path.resolve().is_relative_to(skill) and path.is_file(), 'MANIFEST_PATH')
            raw = path.read_bytes()
            need(len(raw) == item['bytes'] and hashlib.sha256(raw).hexdigest() == item['sha256'], 'MANIFEST_CHANGED')
        for relative, encoded in payload['test_files'].items():
            path = root / 'tests' / relative
            need(path.resolve().is_relative_to(root / 'tests'), 'FIXTURE_PATH')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(encoded, validate=True))
        step = 'offline_regressions'
        sys.path[:0] = [str(skill / 'scripts'), str(root / 'tests')]
        import test_energy_market_daily as tests
        log = io.StringIO()
        result = unittest.TextTestRunner(stream=log).run(unittest.defaultTestLoader.loadTestsFromModule(tests))
        out['offline_tests'] = {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                                'diagnostic_sha256': hashlib.sha256(log.getvalue().encode()).hexdigest()}
        need(result.wasSuccessful(), 'OFFLINE_REGRESSIONS_FAILED')
        import daily
        step = 'synthetic_original_report_render'
        project = root / 'synthetic'
        project.mkdir()
        tests.project_fixture(project)
        report = daily.build(type('Args', (), {'workspace': str(project)})())
        image = next(item for item in report['files'] if item['path'].endswith('.png'))
        sample = Path('/tmp/energy-portable-sample.png')
        sample.write_bytes(Path(image['path']).read_bytes())
        out['render'] = {'png': report['png'], 'render_seconds': report['render_seconds'], 'bytes': image['bytes'], 'sha256': image['sha256'], 'sample_path': str(sample), 'visual_review_pending': True}
        step = 'real_fresh_sources'
        credential = payload['credentials']
        url = credential.get('JKX_API_URL') or credential.get('JKX_API_ENDPOINT')
        password = credential.get('JKX_API_PASSWORD')
        need(isinstance(url, str) and isinstance(password, str) and password, 'CREDENTIAL_FORMAT')
        os.environ['JKX_API_URL'], os.environ['JKX_API_PASSWORD'] = url, password
        os.environ['ENERGY_DAILY_HTTP_PROXY'] = payload['proxy']
        live = root / 'live'
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = daily.main(['prepare', '--workspace', str(live)])
        need(code == 0, 'LIVE_PREPARE_FAILED')
        prepared = json.loads(stdout.getvalue())
        market = json.loads((live / 'market.json').read_text())
        quotes = [q for kind in ('oil', 'gas', 'freight') for q in market[kind]]
        finance = json.loads((live / 'financial.json').read_text())
        research = json.loads((live / 'research.json').read_text())
        out['live'] = {key: prepared.get(key) for key in ('status', 'publication_date', 'data_date', 'prepare_seconds', 'market_data_complete', 'financial_quotes_available', 'market_news_candidates', 'company_news_candidates', 'news_articles_with_original_pages')}
        out['live'].update(provider_requests=market.get('provider_requests'), target_quotes_available=sum(q['status'] == 'available' for q in quotes),
                           target_quotes_explicit_nontrading=sum(q['status'] == 'non_trading' for q in quotes), target_quotes_missing=sum(q['status'] == 'missing' for q in quotes),
                           target_date_fresh_request=True, source_origins_preserved=all(q.get('source') == '金凯讯' for q in quotes),
                           financial_total=len(finance['stocks']) + len(finance['fx']), archived_news=len(research['articles']))
        need(market.get('provider_requests', 0) > 0 and all(q['date'] == prepared['data_date'] for q in quotes), 'FRESH_TARGET_REQUEST_REQUIRED')
        need(sum(q['status'] in ('available', 'non_trading') for q in quotes) == 14, 'PROVIDER_TARGET_NOT_ACCEPTED')
        out.update(status='PORTABLE_ARM64_RENDER_AND_FRESH_PROVIDER_ACCEPTED', candidate_files=len(manifest['files']) + 1, visual_review_pending=True,
                   shared_skill_modified=False, schedule_created=False)
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, QualificationRefused) else 'QUALIFICATION_FAILED_REDACTED', last_step=step)
    finally:
        for key in ('JKX_API_URL', 'JKX_API_PASSWORD'):
            os.environ.pop(key, None)
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
