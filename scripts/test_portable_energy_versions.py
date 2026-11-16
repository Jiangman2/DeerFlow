"""Run every portable regression module in its own Python import namespace.

Historical candidates intentionally use the same unqualified module names.
Combining them in one pytest process tests whichever version was imported first.
Isolation preserves every assertion and each candidate's explicit import guard.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def run(path):
    with tempfile.TemporaryDirectory(prefix='energy-version-tests-') as temporary:
        report = Path(temporary) / 'junit.xml'
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-p', 'no:cacheprovider',
                                 '--confcutdir=portable-skills/tests', '--tb=short', '-q',
                                 '--basetemp=' + str(Path(temporary) / 'work'),
                                 '--junitxml=' + str(report), str(path.relative_to(ROOT))],
                                cwd=ROOT, env=env, capture_output=True, timeout=300)
        row = {'module': path.name, 'exit_code': result.returncode}
        if report.exists():
            suites = ET.parse(report).getroot().findall('testsuite')
            row.update({key: sum(int(s.get(key, '0')) for s in suites)
                        for key in ('tests', 'failures', 'errors', 'skipped')})
        if result.returncode:
            row['diagnostic'] = (result.stdout + result.stderr).decode('utf-8', 'replace')[-3000:]
        return row


if __name__ == '__main__':
    modules = sorted((ROOT / 'portable-skills/tests').glob('test_*.py'))
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(run, modules))
    summary = {'modules': len(rows), 'passed': all(r['exit_code'] == 0 for r in rows),
               'tests': sum(r.get('tests', 0) for r in rows),
               'skipped': sum(r.get('skipped', 0) for r in rows),
               'failures': [r for r in rows if r['exit_code']],
               'production_access': False}
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(not summary['passed'])
