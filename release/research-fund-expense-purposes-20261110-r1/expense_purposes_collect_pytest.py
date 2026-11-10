"""Collect real pytest cases without executing tests; emit per-function counts."""
import collections, contextlib, io, json
import pytest
import expense_purposes_qualification_plan as q
import expense_purposes_qualifier_template as runner
class Collector:
    def pytest_collection_finish(self, session):
        self.cases = sorted([{'case': item.module.__name__+'::'+item.originalname,
                              'parameters': runner.parameter_signature(getattr(getattr(item, 'callspec', None), 'params', {}), item.module)}
                             for item in session.items], key=runner.canonical)
        self.counts = dict(sorted(collections.Counter(item.module.__name__+'::'+item.originalname
                                                     for item in session.items).items()))
if __name__ == '__main__':
    plugin = Collector()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        code = pytest.main(['--collect-only', '-q', '--confcutdir=tests/skills', '-p', 'no:cacheprovider']+list(q.TEST_FILES), plugins=[plugin])
    if code:
        raise SystemExit(code)
    print(json.dumps({'status': 'COLLECTED', 'exitcode': int(code), 'collected': sum(plugin.counts.values()),
                      'collection': plugin.counts, 'cases': plugin.cases, 'cases_sha256': runner.sha(runner.canonical(plugin.cases)), 'test_source_files': q.snapshot_tests()}, sort_keys=True))
