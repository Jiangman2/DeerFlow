"""Render two synthetic samples; no provider, credentials or Work requests."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('html_delivery_samples', HERE / 'test_energy_html_delivery_20261114.py')
tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)
daily = tests.daily
root = tests.REPO / 'portable-skills/validation/energy-market-daily-2.1.4-20261114'
root.mkdir(parents=True, exist_ok=True)
project = root / 'synthetic-project'
project.mkdir(exist_ok=True)
tests.fixtures.project_fixture(project)
data = daily.snapshot(project)
results = []
for name, missing in (('complete', False), ('freight-reference', True)):
    sample = copy.deepcopy(data)
    if missing:
        sample['formal_report_ready'] = False
        sample['quality'] = '数据不完整'
        target = dt.date.fromisoformat(sample['data_date'])
        for quote in sample['freight']:
            quote.update(status='latest_available', date=(target - dt.timedelta(days=2)).isoformat(),
                         quote_age_days=2, target_date=target.isoformat(), target_status='missing')
        sample.update(freight_reference_count=8, freight_reference_search_complete=True)
    path = root / (name + '.html')
    path.write_text(daily.render(sample), encoding='utf-8')
    before = path.read_bytes()
    path.with_suffix('.json').write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding='utf-8')
    path.with_suffix('.txt').write_text(daily.reply_text(sample), encoding='utf-8')
    png = daily.png(path, sample)
    assert path.read_bytes() == before
    prior = tests.REPO / 'portable-skills/validation/energy-market-daily-2.1.3-20261114' / (name + '.png')
    assert path.with_suffix('.png').read_bytes() == prior.read_bytes(), 'PNG_CHANGED_FOR_HTML_ONLY_UPDATE'
    results.append({'name': name, 'png': png, 'png_sha256': daily.digest(prior.read_bytes()),
                    'png_identical_to_213': True, 'html_unchanged_by_screenshot': True,
                    'html_warning_hidden_before_screenshot': not missing or '<aside hidden style="display:none;' in before.decode('utf-8')})
report = {'schema': 'energy-214-html-render.v1', 'samples': results, 'real_browser_used': True,
          'sources': 'SYNTHETIC_LOCAL_FIXTURE', 'provider_requests': 0, 'credentials_read': False,
          'messages_sent': 0, 'production_arm64_qualified': False}
(root / 'RENDER.json').write_text(json.dumps(report, sort_keys=True, indent=2), encoding='utf-8')
print(json.dumps(report, sort_keys=True))
