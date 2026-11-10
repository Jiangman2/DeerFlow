"""Record completed offline checks; production acceptance remains pending."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
names = ['energy_market_collector.py', 'fresh_target_probe.py',
         'repair-energy-market-proxy-20261110-r1.py', 'test_energy_market_collector.py',
         'test_fresh_target_probe.py', 'test_fresh_target_linux.py',
         'test_repair_energy_market_proxy.py', 'RELEASE.txt']
files = {}
for name in names:
    raw = (root/name).read_bytes()
    files[name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
record = {
    'schema': 'energy-proxy-repair-offline-validation.v1',
    'files': files,
    'windows': {'tests': 71, 'passed': 69, 'skipped_linux_only': 2, 'failed': 0},
    'linux': {'tests': 71, 'passed': 71, 'failed': 0, 'container_network': 'none',
              'image': 'portal-finance-feedback-test:20261104-r1'},
    'python37_entry_guard': {'status': 'PASS', 'expected_error': 'DATAX01_ROOT_REQUIRED',
                            'once_claimed': False, 'patch_requests': 0},
    'test_provider_requests': 0,
    'test_real_credentials_used': False,
    'original_engine_sha256_checks': 'PASS_158_FILES_UNCHANGED',
    'real_linux_private_copy_and_subprocess_synthetic_http': 'PASS',
    'production_proxy_without_credentials': 'HTTP_200_OBSERVED_BY_OPERATOR',
    'production_candidate_installed': False,
    'production_fresh_target_acceptance': 'PENDING_OPERATOR_EXECUTION',
    'formal_report_ready': False,
    'schedule_created': False,
    'work_messages_sent': 0,
}
(root/'VALIDATION.json').write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)+'\n', encoding='utf-8')
name = 'repair-energy-market-proxy-20261110-r1.py'
(root/'energy-market-proxy-20261110-r1-SHA256SUMS.txt').write_text(
    files[name]['sha256'] + '  ' + name + '\n', encoding='ascii')
print(json.dumps({'status': 'OFFLINE_VALIDATION_RECORDED_PRODUCTION_PENDING', 'operator_tool': files[name]}))
