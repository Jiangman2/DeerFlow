"""Launch only offline Docker qualification; never contact production."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location('runtime_ready_builder', HERE / 'build_activation.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('qualify', 'verify'))
    parser.add_argument('--platform', choices=('arm64', 'amd64'), default='arm64')
    parser.add_argument('--diagnostic', action='store_true')
    options = parser.parse_args()
    raw, _, code = builder.build()
    host = {'__name__': 'runtime_definitions'}
    exec(compile(raw, '<host>', 'exec'), host)
    docker = 'C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
    args = [docker, 'run', '--rm', '--platform', 'linux/' + options.platform,
            '--network', 'none', '--read-only', '--tmpfs', '/tmp',
            '-e', 'PYTHONDONTWRITEBYTECODE=1', '-v', str(ROOT) + ':/work:ro', '-w', '/work']
    if options.diagnostic:
        args += ['-e', 'QUALIFIER_DIAGNOSTIC=1']
    retained = ROOT / 'release/post51-energy-production-sync-20261116/deployed-overlay'
    for path, row in host['EXPECTED_SOURCES'].items():
        if not path.endswith('.py'):
            continue
        source = retained / path.removeprefix('/app/')
        if path == host['TARGET'] and options.mode == 'verify':
            source = HERE / 'backend/app/scheduler/work_notification_delivery.py'
            row = code
        data = source.read_bytes()
        assert {'bytes': len(data), 'sha256': host['sha'](data)} == row, path
        args += ['-v', str(source) + ':' + path + ':ro']
    args += ['--entrypoint', '/app/backend/.venv/bin/python',
             'deerflow-p6-gateway418:20261115-r236-' + options.platform,
             '-I', '-B', 'portable-skills/deployment/personal-notification-ready-20261116-r1/check_audited_child.py', options.mode]
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if result.returncode:
        print(result.stdout.decode(errors='replace')[-8000:])
        print(result.stderr.decode(errors='replace')[-4000:])
        raise SystemExit(result.returncode)
    row = json.loads(result.stdout.decode().splitlines()[-1])
    assert row['status'] == 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED'
    assert row['isolated_notification_cases'] == 10 and row['postgres_query_compiled'] is True
    assert row['work_messages_sent'] == 0 and row['database_access'] is False
    print(json.dumps({'status': row['status'], 'platform': options.platform, 'mode': options.mode,
                      'cases': row['isolated_notification_cases'], 'actual_database_access': False,
                      'local_cached_library_fixture': row['versions'],
                      'production_version_pins_unchanged': True,
                      'network': 'none', 'work_messages_sent': 0}))


if __name__ == '__main__':
    main()
