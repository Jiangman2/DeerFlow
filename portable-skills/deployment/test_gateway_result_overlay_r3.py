"""Run focused tests against exact overlay modules and the current baseline."""
import importlib.abc
import importlib.util
from pathlib import Path
import sys
import uuid
import os

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / '.codex-energy-daily-efficient-20261111/backend'
OVERLAY = Path(__file__).resolve().parent / 'gateway-link-r3/backend'
MODULES = {
    'app.channels.manager': 'app/channels/manager.py',
    'app.integrations.work.notifications': 'app/integrations/work/notifications.py',
    'app.scheduler.work_notification_delivery': 'app/scheduler/work_notification_delivery.py',
    'app.scheduler.work_notifications': 'app/scheduler/work_notifications.py',
    'deerflow.tools.builtins.present_file_tool': 'packages/harness/deerflow/tools/builtins/present_file_tool.py',
    'deerflow.utils.conversation_url': 'packages/harness/deerflow/utils/conversation_url.py',
}


class ExactOverlay(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in MODULES:
            return importlib.util.spec_from_file_location(fullname, OVERLAY / MODULES[fullname])
        return None


if __name__ == '__main__':
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(BASE), str(BASE / 'packages/harness')]
    sys.meta_path.insert(0, ExactOverlay())
    import pytest
    temp_root = Path('/tmp') if os.name == 'posix' else ROOT
    temporary = temp_root / ('.codex-link-r3-pytest-' + uuid.uuid4().hex)
    if temporary.exists() or not temporary.resolve().is_relative_to(temp_root.resolve()):
        raise RuntimeError('TEST_TEMPORARY_PATH')
    raise SystemExit(pytest.main([str(OVERLAY / 'tests'), str(BASE / 'tests/test_present_file_tool_core_logic.py'), '-q', '--tb=short', '--basetemp', str(temporary), '-p', 'no:cacheprovider']))
