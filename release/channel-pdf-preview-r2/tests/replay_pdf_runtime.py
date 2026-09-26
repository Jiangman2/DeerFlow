"""Run the PDF regression suite against the rebased production artifact module.

Uses existing local dependencies; this is not a substitute for ARM64 image smoke.
No checkout source is overwritten. The user PDF remains local test data.
"""
import hashlib
import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent.parent
sys.path.insert(0, str(REPO / 'backend'))
sys.path.insert(0, str(REPO / 'backend/packages/harness'))
spec = importlib.util.spec_from_file_location('r2_package', ROOT / 'package.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)
_, _, targets, _, _ = package.load_inputs(
    Path.home() / 'Downloads/deerflow-channel-pdf-preview-20260923-r1.tar.gz',
    Path.home() / 'Downloads/channel-pdf-baseline-audit-20260923T113615Z.json')


class Replay:
    def pytest_sessionstart(self, session):
        source = targets['gateway']['app/gateway/routers/artifacts.py']
        name = 'app.gateway.routers.artifacts'
        module = types.ModuleType(name)
        module.__file__ = str(REPO / 'backend/app/gateway/routers/artifacts.py')
        module.__package__ = 'app.gateway.routers'
        exec(compile(source, '<audited-r2-artifacts>', 'exec'), module.__dict__)
        sys.modules[name] = module
        setattr(importlib.import_module('app.gateway.routers'), 'artifacts', module)
        quote = Path.home() / 'Downloads/quote.pdf'
        before = hashlib.sha256(quote.read_bytes()).hexdigest()
        assert before == '70532d4c74046506d9cf2d16c070400bfc89b8d68cb14a06d1651dae43c0428b'
        checked = module._validate_pdf_preview_artifact(quote, 'mnt/user-data/outputs/quote.pdf')
        assert module._render_pdf_preview_page(checked, 0).startswith(b'\x89PNG\r\n\x1a\n')
        assert hashlib.sha256(quote.read_bytes()).hexdigest() == before
        print('audited_r2_quote_native_preview=PASS original_unchanged=yes local_dependencies_only=yes')


if __name__ == '__main__':
    sys.exit(pytest.main([str(REPO / 'backend/tests/test_pdf_preview_object_streams.py'),
                         '-q', '-p', 'no:cacheprovider'], plugins=[Replay()]))
