"""Run exact sealed regressions and composition against this candidate locally."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('webhook_candidate_builder', HERE / 'seal.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
files, metadata = builder.build()
host = {'__name__': 'sealed_webhook_local_check'}
exec(compile(files[builder.NAME], '<host>', 'exec'), host)
assets = host['load_assets']()
verifier = {'__name__': 'sealed_webhook_qualifier_check'}
exec(compile(assets['verifier'], '<verifier>', 'exec'), verifier)
verifier['SKILL'] = builder.SKILL
with tempfile.TemporaryDirectory(prefix='energy-webhook-sealed-check-') as location:
    root = Path(location)
    verifier['write_fixtures'](json.loads(assets['test_files']), root)
    sys.path[:0] = [str(root / 'tests'), str(builder.SKILL / 'scripts')]
    original_run = verifier['unittest'].TextTestRunner.run
    def checked_run(runner, suite):
        result = original_run(runner, suite)
        if not result.wasSuccessful():
            print(runner.stream.getvalue())
        return result
    with patch.object(verifier['unittest'].TextTestRunner, 'run', checked_run):
        offline = verifier['offline_regressions'](root)
    fixture = root / 'tests/test_energy_work_preview_20261115.py'
    source = fixture.read_text(encoding='utf-8').replace("SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.8'",
        'SKILL = Path(' + repr(str(builder.SKILL)) + ')')
    fx = ModuleType('sealed_webhook_fixtures'); fx.__file__ = str(fixture)
    exec(compile(source, str(fixture), 'exec'), fx.__dict__)
    project = root / 'project'; project.mkdir(); fx.fixtures.project_fixture(project)
    with patch.dict(os.environ, {'DEERFLOW_CONVERSATION_URL': verifier['SYNTHETIC_CONVERSATION_URL']}), patch.object(fx.daily, 'png', side_effect=fx.mock_png):
        report = fx.daily.build(SimpleNamespace(workspace=str(project), visual_reviewed=False, publication_receipt=None))
        composed = verifier['qualify_final_text'](fx.daily, project, report, root)
        freight = verifier['qualify_freight_render'](fx.daily, project, root)
    print(json.dumps({'status': 'SEALED_PUBLICATION_REGRESSIONS_AND_COMPOSITION_PASSED', 'offline_tests': offline,
        'linked_text_unchanged': composed['reader_link_survives_two_2000_character_previews'],
        'freight_render_probe': freight['status'], 'png_renderer_mocked': True,
        'real_browser_render_proven': False, 'real_webhook_send_proven': False, 'work_messages_sent': 0}))
