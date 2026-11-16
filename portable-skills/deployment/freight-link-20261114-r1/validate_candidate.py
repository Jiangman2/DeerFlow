"""Offline candidate regressions and optional synthetic PNG output, no live data."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.1'
TESTS = REPO / 'portable-skills/tests'
sys.path[:0] = [str(SKILL / 'scripts'), str(TESTS)]
sys.dont_write_bytecode = True
import daily


def load_test(name, source=None):
    path = TESTS / (name + '.py')
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    text = path.read_text(encoding='utf-8') if source is None else source
    exec(compile(text, str(path), 'exec'), module.__dict__)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--render-directory')
    parser.add_argument('--report-json')
    args = parser.parse_args()
    assert Path(daily.__file__).resolve() == SKILL / 'scripts/daily.py'
    assert daily.VERSION == '2.1.1-portable'
    original = (TESTS / 'test_energy_market_daily.py').read_text(encoding='utf-8')
    marker = "SKILL = Path(__file__).resolve().parents[1] / 'energy-market-daily'"
    assert original.count(marker) == 1
    original = original.replace(marker, 'SKILL = Path(' + repr(str(SKILL)) + ')')
    fixture = load_test('test_energy_market_daily', original)
    modules = [fixture] + [load_test(name) for name in (
        'test_energy_browser_runtime', 'test_energy_failure_handling',
        'test_energy_freight_latest_20261114', 'test_energy_freight_links_delivery_20261114')]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in modules)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    report = {'version': daily.VERSION, 'tests': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'skipped': len(result.skipped), 'success': result.wasSuccessful(),
              'candidate': str(SKILL), 'provider_requests': 0, 'messages_sent': 0}
    if result.wasSuccessful() and args.render_directory:
        directory = Path(args.render_directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='energy-211-render-') as location:
            temporary = Path(location)
            project = temporary / 'project'
            project.mkdir()
            fixture.project_fixture(project)
            baseline = daily.build(types.SimpleNamespace(workspace=str(project)))
            spec = importlib.util.spec_from_file_location('energy_211_compose_probe', HERE / 'compose_probe.py')
            probe = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(probe)
            report['composition'] = probe.qualify(daily, project, baseline, temporary)
            report['freight_render'] = probe.qualify_freight_render(daily, project, temporary)
            for name, source_project in (('complete', project), ('freight-fallback', temporary / 'freight-render')):
                receipt = daily.load(source_project / 'RESULT.json')
                for item in receipt['files']:
                    source = Path(item['path'])
                    shutil.copyfile(source, directory / (name + source.suffix))
            report['samples'] = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                 for p in directory.iterdir() if p.is_file() and p.suffix in ('.png', '.html', '.json', '.txt')}
    if args.report_json:
        Path(args.report_json).write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return int(not result.wasSuccessful())


if __name__ == '__main__':
    sys.exit(main())
