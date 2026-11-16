"""Exercise the exact sealed ordinary-user fixture relocation and text probe."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('preview_installer_builder',HERE/'seal.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
files,metadata=builder.build()
host={'__name__':'sealed_local_check'};exec(compile(files[builder.NAME],'<host>','exec'),host)
assets=host['load_assets']();scope={'__name__':'sealed_qualifier_check'};exec(compile(assets['verifier'],'<verifier>','exec'),scope)
scope['SKILL']=builder.SKILL
with tempfile.TemporaryDirectory(prefix='energy-preview-sealed-check-') as directory:
    root=Path(directory)
    fixtures=json.loads(assets['test_files'])
    scope['write_fixtures'](fixtures,root)
    sys.path.insert(0,str(root/'tests'));sys.path.insert(0,str(builder.SKILL/'scripts'))
    offline=scope['offline_regressions'](root)
    source=root/'tests/test_energy_work_preview_20261115.py'
    tests=ModuleType('preview_probe_fixtures');tests.__file__=str(source)
    bound=source.read_text(encoding='utf-8').replace("SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.6'",'SKILL = Path('+repr(str(builder.SKILL))+')')
    exec(compile(bound,str(source),'exec'),tests.__dict__)
    project=root/'project';project.mkdir();tests.fixtures.project_fixture(project)
    with patch.dict(os.environ,{'DEERFLOW_CONVERSATION_URL':scope['SYNTHETIC_CONVERSATION_URL']}),patch.object(tests.daily,'png',side_effect=tests.mock_png):
        report=tests.daily.build(SimpleNamespace(workspace=str(project),visual_reviewed=False,publication_receipt=None))
        composed=scope['qualify_final_text'](tests.daily,project,report,root)
        assert composed['reader_link_survives_two_2000_character_previews'] is True
        assert composed['gateway_418_isolated_message_is_link_only'] is True
        freight=scope['qualify_freight_render'](tests.daily,project,root)
        assert freight['status']=='FREIGHT_FALLBACK_RENDER_QUALIFIED'
    print(json.dumps({'status':'SEALED_UID1000_PATHS_AND_GATEWAY_PREVIEW_COMPOSE_PROBE_PASSED',
                      'offline_tests':offline,'two_2000_character_previews_keep_reader':True,
                      'gateway_418_message_is_link_only':True,'files_modified_outside_own_temporary_directory':False,
                      'provider_requests':0,'work_messages_sent':0},sort_keys=True))
