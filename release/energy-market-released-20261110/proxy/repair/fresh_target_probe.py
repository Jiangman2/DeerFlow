"""One fresh target-day fetch in a disposable copy using the deployed proxy runner.

Executed only inside the dedicated collector by the authorized release tool.
Never updates the shared feed, original package, or collector in-memory state.
"""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import types

CODE = Path('/opt/collector/energy_market_collector.py')
SKILL = Path('/opt/energy-skill')
ENTRY_SHA = '91e6fcd4f9f6bba3307ae166b9e0188b6f6c816008db3aba8382cd23bff93f8f'
SKILL_SHA = '8bbeb3993343c9955f40416e9b952894d45cff94968ad3bc1fe340998eece95d'
PROXY = 'http://10.68.211.1:8080'

# Only a synthetic owning-main is substituted in this private subprocess. The
# supplier fetch, normalize, retries and date validation remain the original code.
SINGLE_DAY = r'''
import hashlib,json,os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import fetch_market_history as original
probe_day=sys.argv[2]
probe_result=Path(sys.argv[1]).parent.parent/'fresh-observation.json'
def one_original_fetch():
 summary={'http_requests':0,'http_statuses':[],'target_response_fresh':False}
 transport=original.requests.get
 def counted(url,**kwargs):
  summary['http_requests']+=1
  if summary['http_requests']>2:raise ValueError('FRESH_REQUEST_LIMIT')
  response=transport(url,**kwargs)
  summary['http_statuses'].append(response.status_code)
  summary['decoded_response_sha256']=hashlib.sha256(response.content).hexdigest()
  return response
 original.requests.get=counted
 try:
  records=original.fetch(probe_day)
  required=set(original.SPECS)|set(original.FREIGHT)
  if len(required)!=14:raise ValueError('ORIGINAL_SERIES_CHANGED')
  selected=[r for r in records if r['code'] in required]
  if len(selected)!=14:raise ValueError('REQUIRED_SERIES_MISSING')
  summary['quotes']={r['code']:r['status'] for r in selected}
  summary['target_response_fresh']=summary['http_requests']>0
  summary['original_normalizer_passed']=True
 finally:
  original.requests.get=transport
  probe_result.write_text(json.dumps(summary,sort_keys=True),encoding='utf-8')
original.main=one_original_fetch
'''


def require(ok, code):
    if not ok:
        raise ValueError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verified_code(expected):
    require(bool(re.fullmatch(r'[0-9a-f]{64}', expected)), 'CODE_HASH_FORMAT')
    # A ConfigMap uses symlinks; only resolve inside its fixed mount.
    actual = CODE.resolve(strict=True)
    require(CODE.parent in actual.parents, 'CODE_MOUNT_ESCAPE')
    st = actual.stat()
    require(stat.S_ISREG(st.st_mode) and st.st_size <= 65536, 'CODE_SIZE_OR_TYPE')
    raw = actual.read_bytes()
    require(digest(raw) == expected, 'DEPLOYED_COLLECTOR_CHANGED')
    module = types.ModuleType('verified_deployed_collector')
    module.__file__ = str(CODE)
    exec(compile(raw, str(CODE), 'exec'), module.__dict__)
    require(module.SKILL == SKILL and module.PRIVATE == Path('/private'), 'FIXED_MOUNTS_REQUIRED')
    require(module.configured_proxy() == PROXY, 'FIXED_PROXY_REQUIRED')
    return module


def inspect_summary(observation, runner):
    require(runner.get('status') == 'ORIGINAL_MARKET_COMPLETE', 'FRESH_ORIGINAL_FETCH_FAILED')
    require(1 <= runner.get('connections', 0) <= 2, 'FRESH_PROXY_CONNECTION_REQUIRED')
    require(observation.get('target_response_fresh') is True, 'FRESH_TARGET_REQUIRED')
    require(observation.get('original_normalizer_passed') is True, 'ORIGINAL_NORMALIZER_REQUIRED')
    require(1 <= observation.get('http_requests', 0) <= 2, 'FRESH_REQUEST_COUNT')
    require(observation.get('http_statuses', [])[-1:] == [200], 'FRESH_HTTP_SUCCESS_REQUIRED')
    require(bool(re.fullmatch(r'[0-9a-f]{64}', observation.get('decoded_response_sha256', ''))), 'RESPONSE_HASH_REQUIRED')
    required = {'LD0221', 'XA0008', 'NY0121', 'NY0421', 'XA0942', 'LD1221',
                'LPGD59', 'LPGD60', 'LPG1225', 'LPG1224', 'LPG1179', 'LPG1177', 'LPG1227', 'LPG1226'}
    quotes = observation.get('quotes', {})
    require(set(quotes) == required, 'EXPECTED_SERIES_REQUIRED')
    require(set(quotes.values()) <= {'available', 'missing', 'non_trading'}, 'QUOTE_STATUS_REQUIRED')
    return {
        'status': 'FRESH_TARGET_RESPONSE_VALIDATED',
        'target_response_fresh': True,
        'target_received_via_proxy': True,
        'original_normalizer_passed': True,
        'http_requests': observation['http_requests'],
        'http_statuses': observation['http_statuses'],
        'decoded_response_sha256': observation['decoded_response_sha256'],
        'proxy_connections': runner['connections'],
        'quote_counts': {status: list(quotes.values()).count(status) for status in ('available', 'missing', 'non_trading')},
        'unavailable_codes': sorted(code for code, status in quotes.items() if status != 'available'),
        'all_required_quotes_available': all(status == 'available' for status in quotes.values()),
    }


def probe(expected_hash, day, out):
    collector = verified_code(expected_hash)
    collector.date_window(day)
    entry = collector.load_entry()
    require(digest(entry.read(SKILL / 'scripts/entry.py')) == ENTRY_SHA, 'ENTRY_CHANGED')
    require(digest(entry.read(SKILL / 'SKILL.md')) == SKILL_SHA, 'SKILL_CHANGED')
    expected = entry.original_files()
    out['original_files_verified'] = len(expected)
    key = collector.credential()
    out['credentials_read_by_dedicated_probe'] = True
    with tempfile.TemporaryDirectory(prefix='fresh-target-', dir=collector.PRIVATE) as work:
        engine = Path(work) / 'engine'
        shutil.copytree(SKILL / 'upstream', engine)
        entry.verify_tree(engine, expected, False)
        target = engine / 'app/sources' / ('jkx_new_' + day + '.json')
        # Removing this private-copy cache forces the original fetch to use HTTP.
        # No shared feed or read-only original path is deleted or modified.
        if target.exists():
            require(target.is_file() and not target.is_symlink(), 'PRIVATE_CACHE_TYPE')
            target.unlink()
        out['private_target_cache_absent_before_fetch'] = not target.exists()
        env = {k: os.environ[k] for k in ('PATH', 'HOME', 'LANG', 'LD_LIBRARY_PATH') if k in os.environ}
        env.update(key)
        env.update(ENERGY_MARKET_HTTP_PROXY=PROXY, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
        with tempfile.TemporaryFile() as log:
            child = subprocess.Popen(
                [sys.executable, '-I', '-B', '-c', SINGLE_DAY + collector.RUNNER,
                 str(engine / 'app'), day, '2'],
                env=env, stdout=log, stderr=log, start_new_session=True,
            )
            try:
                child.wait(timeout=70)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
                raise ValueError('FRESH_PROBE_TIMEOUT') from None
            require(child.returncode == 0 and log.tell() <= 8192, 'FRESH_PROCESS_FAILED')
            log.seek(0)
            runner = json.loads(log.read(8193))
        out['runner'] = {k: runner[k] for k in ('status', 'connections', 'error_class', 'error_code', 'failure_stage') if k in runner}
        entry.verify_tree(engine, expected, True)
        summary_path = Path(work) / 'fresh-observation.json'
        if summary_path.is_file():
            observation = json.loads(entry.read(summary_path, 8192))
            out.update(inspect_summary(observation, runner))
        else:
            raise ValueError('FRESH_OBSERVATION_MISSING')
        require(target.is_file(), 'FRESH_VALIDATED_TARGET_FILE_REQUIRED')
        collector.load_entry().original_files()
    out['temporary_copy_cleaned'] = True


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out = {'schema': 'energy-proxy-fresh-target.v1', 'status': 'INCOMPLETE',
           'configuration_modified': False, 'shared_cache_modified': False,
           'original_skill_modified': False, 'credentials_exported': False,
           'raw_response_exported': False, 'raw_errors_exported': False,
           'credentials_read_by_dedicated_probe': False, 'max_http_requests': 2,
           'work_messages_sent': 0, 'real_report_generated': False}
    try:
        require(len(argv) == 2, 'FIXED_HASH_AND_DAY_REQUIRED')
        out['data_date'] = argv[1]
        probe(argv[0], argv[1], out)
    except Exception as exc:
        out['status'] = 'INCOMPLETE'
        out['error_class'] = type(exc).__name__
        text = str(exc)
        if isinstance(exc, ValueError) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,90}', text):
            out['error_code'] = text
    print(json.dumps(out, sort_keys=True))
    return 0 if out['status'] == 'FRESH_TARGET_RESPONSE_VALIDATED' else 1


if __name__ == '__main__':
    sys.exit(main())
