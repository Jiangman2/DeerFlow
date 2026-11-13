"""Run the existing offline behavior suite against the isolated 2.1.0 candidate."""
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[3]
CANDIDATE = REPO / 'portable-skills/candidates/energy-market-daily-2.1.0'
SOURCE = REPO / 'portable-skills/tests/test_energy_market_daily.py'
old = "SKILL = Path(__file__).resolve().parents[1] / 'energy-market-daily'"
text = SOURCE.read_text(encoding='utf-8')
if text.count(old) != 1:
    raise SystemExit('test binding changed')
namespace = {'__name__': 'candidate_portable_regressions', '__file__': str(SOURCE)}
exec(compile(text.replace(old, 'SKILL = Path(' + repr(str(CANDIDATE)) + ')'), str(SOURCE), 'exec'), namespace)
suite = unittest.TestSuite()
for name, value in namespace.items():
    if isinstance(value, type) and issubclass(value, unittest.TestCase):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
result = unittest.TextTestRunner(verbosity=1).run(suite)
success = result.wasSuccessful()

for source, old, new in [
    (REPO / 'portable-skills/tests/test_energy_browser_runtime.py',
     "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'energy-market-daily/scripts'))",
     'sys.path.insert(0, ' + repr(str(CANDIDATE / 'scripts')) + ')'),
    (REPO / 'portable-skills/diagnostics/energy-market-daily-2.0.3-receipts-r1/test_candidate_receipts.py',
     'CANDIDATE = REPO / "portable-skills/candidates/energy-market-daily-2.0.3/scripts/daily.py"',
     'CANDIDATE = Path(' + repr(str(CANDIDATE / 'scripts/daily.py')) + ')'),
]:
    text = source.read_text(encoding='utf-8')
    if text.count(old) != 1:
        raise SystemExit('supporting test binding changed')
    namespace = {'__name__': 'candidate_supporting_regressions', '__file__': str(source)}
    exec(compile(text.replace(old, new), str(source), 'exec'), namespace)
    suite = unittest.TestSuite()
    for value in namespace.values():
        if isinstance(value, type) and issubclass(value, unittest.TestCase):
            suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(value))
    success = unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() and success
raise SystemExit(not success)
