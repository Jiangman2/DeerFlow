"""Run the sealed regression subset against installed code and synthetic storage."""
import base64
import contextlib
import hashlib
import importlib
import io
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
import warnings
import zlib

TEST_BUNDLE = None
TEST_SHA256 = None
TARGETS = None


class Refused(RuntimeError): pass


def require(value, code):
    if not value: raise Refused(code)


class Sink(io.TextIOBase):
    def write(self, value): return len(value)
    def flush(self): pass
    def isatty(self): return False
    @property
    def encoding(self): return 'utf-8'


class Counts:
    def __init__(self): self.passed = 0; self.failed = 0; self.skipped = 0; self.errors = 0
    def pytest_runtest_logreport(self, report):
        if report.when == 'call' and report.passed: self.passed += 1
        if report.failed: self.failed += 1
        if report.skipped: self.skipped += 1
    def pytest_collectreport(self, report):
        if report.failed: self.errors += 1


def qualify(pins, result):
    root = Path('/app/backend')
    require(len(pins) == 588, 'SOURCE_INVENTORY_SIZE')
    for relative, expected in pins.items():
        require(relative.startswith(('app/', 'packages/harness/')) and '..' not in Path(relative).parts, 'SOURCE_PATH')
        source = root / relative
        require(source.is_file() and not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest() == expected, 'INSTALLED_SOURCE_MISMATCH')
    actual = {p.relative_to(root).as_posix() for base in (root/'app', root/'packages/harness') for p in base.rglob('*.py') if '__pycache__' not in p.parts}
    require(actual == set(pins), 'INSTALLED_SOURCE_SET_MISMATCH')
    result['installed_source_files'] = len(pins)
    result['checks'].append('all_installed_sources_match_sealed_batch')
    raw = zlib.decompress(base64.b64decode(TEST_BUNDLE))
    require(hashlib.sha256(raw).hexdigest() == TEST_SHA256 and len(raw) < 400000, 'TEST_BUNDLE_CHANGED')
    files = json.loads(raw)
    with tempfile.TemporaryDirectory(prefix='combined-installed-') as temp:
        work = Path(temp)
        for name, content in files.items():
            require(name.startswith(('backend/tests/', 'deploy/enterprise/')) and '..' not in Path(name).parts, 'TEST_PATH')
            path = work/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content, encoding='utf-8')
        (work/'config.yaml').write_text('models: []\n', encoding='utf-8')
        os.environ.clear()
        os.environ.update(HOME=temp, TMPDIR=temp, DEER_FLOW_HOME=str(work/'userdata'),
            DEER_FLOW_CONFIG_PATH=str(work/'config.yaml'),
            PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', LANGCHAIN_TRACING_V2='false', LANGSMITH_TRACING='false', OTEL_SDK_DISABLED='true')
        sys.path[:0] = [str(root), str(root/'packages/harness'), str(work/'backend/tests')]
        # Block real IP networking before importing any application module.
        original_connect = socket.socket.connect
        def guarded_connect(sock, *args, **kwargs):
            if sock.family in (socket.AF_INET, socket.AF_INET6): raise Refused('NETWORK_FORBIDDEN')
            return original_connect(sock, *args, **kwargs)
        def denied(*args, **kwargs): raise Refused('NETWORK_FORBIDDEN')
        with patch.object(socket.socket, 'connect', guarded_connect), patch.object(socket.socket, 'connect_ex', denied), patch.object(socket, 'getaddrinfo', denied):
            modules = ('app.gateway.competition_display_store', 'app.gateway.routers.employee_analytics',
                'app.gateway.routers.competition_usage', 'deerflow.models.cnooc_deepseek_v41',
                'deerflow.tools.builtins.local_data_tool', 'deerflow.runtime.datalake_policy')
            for name in modules:
                result['phase'] = 'IMPORT_' + name
                module = importlib.import_module(name)
                require(Path(module.__file__).resolve().is_relative_to(root), 'IMPORT_NOT_INSTALLED')
            result['checks'].append('installed_modules_import_without_production_configuration')
            import pytest
            counts = Counts()
            result['phase'] = 'SYNTHETIC_REGRESSIONS'
            args = [str(work/'backend/tests'/name) for name in TARGETS]
            exit_code = pytest.main(args + ['-q', '--tb=no', '-p', 'pytest_asyncio.plugin', '-p', 'no:cacheprovider', '-o', 'asyncio_mode=auto'], plugins=[counts])
            result['tests'] = dict(passed=counts.passed, failed=counts.failed, skipped=counts.skipped, collection_errors=counts.errors, exitcode=int(exit_code))
            require(exit_code == 0 and counts.passed >= 120 and counts.failed == counts.errors == counts.skipped == 0, 'REGRESSION_CHECK_FAILED')
            result['checks'].append('synthetic_rbac_analytics_model_store_and_task_boundary_tests')
    result.update(temporary_files_cleaned=True, status='INSTALLED_COMBINED_FEATURES_ACCEPTED', phase='COMPLETE')


def main():
    result = dict(status='INCOMPLETE', phase='VERIFY_SOURCES', checks=[], production_database_access=False,
        production_configuration_used=False, provider_requests=0, business_requests=0,
        production_workload_modified=False, candidate_only_in_job=True, application_started=False,
        schema_migrated=False, credentials_exported=False, raw_errors_exported=False, ready_for_rollout=False)
    logging.disable(logging.CRITICAL); warnings.simplefilter('ignore')
    try:
        # pytest expects a real TextIO surface (encoding/fileno/isatty).
        # Keep its diagnostics inside bounded Job scratch storage and close them.
        with tempfile.TemporaryFile(mode='w+', encoding='utf-8') as private:
            with contextlib.redirect_stdout(private), contextlib.redirect_stderr(private):
                qualify(json.loads(sys.argv[1]), result)
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'INSTALLED_CHECK_FAILED')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'INSTALLED_COMBINED_FEATURES_ACCEPTED' else 1


if __name__ == '__main__': raise SystemExit(main())
