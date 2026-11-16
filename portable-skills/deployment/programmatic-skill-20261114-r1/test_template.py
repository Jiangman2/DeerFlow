"""Local-only template qualification; no production or release writes."""
import base64
import contextlib
import importlib.util
import io
import os
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location('programmatic_template_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seal, verifier, probe = (load(name) for name in ('seal', 'verify_child', 'compose_probe'))


def fixture_bytes():
    result = {name:(REPO/'portable-skills/tests'/name).read_bytes() for name in seal.FIXTURE_NAMES}
    for name in seal.PRIOR_SOURCE_NAMES:
        result['published-2.1.2/scripts/'+name]=(REPO/'portable-skills/candidates/energy-market-daily-2.1.2/scripts'/name).read_bytes()
    return result


class TemplateTests(unittest.TestCase):
    def test_accepted_real_gateway_receipt_remains_read_only_input(self):
        path=REPO/'release/task-conversation-context-postcheck-20261114-r2/OPERATOR-RUNTIME-ACCEPTANCE.json'
        before=path.read_bytes()
        contract,binding=seal.gateway_receipt(path)
        self.assertEqual(contract['bytes'],4064)
        self.assertEqual(contract['sha256'],'e139a6a2d05b797f699aa094a3f92886473731a6c323a5267e7e39cff3e994b7')
        self.assertEqual(binding['gateway']['generation'],417)
        self.assertEqual(path.read_bytes(),before)

    def test_unfrozen_candidate_cannot_be_packaged(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(seal,'SOURCE_REVIEW',Path(directory)/'absent.json'):
            with self.assertRaisesRegex(ValueError,'CANDIDATE_SOURCE_REVIEW_REQUIRED'):
                seal.source_files()

    def test_temporary_sealing_binds_all_current_assets_without_release_writes(self):
        fixture=fixture_bytes()
        files={path.relative_to(seal.CANDIDATE).as_posix():path.read_bytes()
               for path in seal.CANDIDATE.rglob('*') if path.is_file()
               and '__pycache__' not in path.relative_to(seal.CANDIDATE).parts and path.suffix != '.pyc'
               and path.relative_to(seal.CANDIDATE).as_posix() not in seal.EXCLUDED_LOCAL_FILES}
        reviewed={'schema':'energy-programmatic-reviewed-source.v1','version':seal.VERSION,
                  'offline_suites':seal.OFFLINE_SUITES,'offline_test_count':seal.OFFLINE_TEST_COUNT,
                  'files':{name:{'bytes':len(raw),'sha256':seal.sha(raw)} for name,raw in files.items()},
                  'fixtures':{name:{'bytes':len(raw),'sha256':seal.sha(raw)} for name,raw in fixture.items()}}
        cores={name:{'prior_sha256':seal.sha(fixture['published-2.1.2/scripts/'+name]),
                     'candidate_sha256':seal.sha(files['scripts/'+name])} for name in seal.PRIOR_SOURCE_NAMES if name!='daily.py'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            candidate=root/'candidate';candidate.mkdir()
            for name,raw in files.items():
                destination=candidate/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(raw)
            for name in ('operator.py','publisher.py','verify_child.py','compose_probe.py','seal.py'):
                (root/name).write_bytes((HERE/name).read_bytes())
            review=root/'REVIEWED-SOURCE.json';review.write_text(json.dumps(reviewed),encoding='utf-8')
            (root/'CORE-SOURCE-PRESERVATION.json').write_text(json.dumps({'files':cores}),encoding='utf-8')
            receipt=REPO/'release/task-conversation-context-postcheck-20261114-r2/OPERATOR-RUNTIME-ACCEPTANCE.json'
            with patch.object(seal,'HERE',root),patch.object(seal,'SOURCE_REVIEW',review),patch.object(seal,'CANDIDATE',candidate):
                output,metadata=seal.build(receipt)
            self.assertEqual(metadata['offline_test_count'],27)
            self.assertEqual(metadata['accepted_old_tree_sha256'],'915a4c765fc81e20b054bc77aa08ce9e919599d354a2aee7edc19db6637c1357')
            self.assertFalse(metadata['gateway_modified'])
            scope={'__name__':'temporary_sealed_installer'}
            exec(compile(output['install-energy-programmatic-skill-20261114-r1.py'],'<temporary-sealed>','exec'),scope)
            self.assertEqual(set(scope['load_assets']()),{'publisher','verifier','package','test_files'})
            self.assertEqual(scope['OFFLINE_TEST_COUNT'],27)
            self.assertEqual(scope['GATEWAY_CONTEXT_RECEIPT']['bytes'],4064)

    def test_relocated_offline_suites_bind_current_and_immutable_old_sources(self):
        files=fixture_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.object(verifier,'FIXTURES',{name:verifier.sha(raw) for name,raw in files.items()}):
                verifier.write_fixtures({name:base64.b64encode(raw).decode('ascii') for name,raw in files.items()},root)
            with patch.object(verifier,'SKILL',seal.CANDIDATE),patch.object(verifier,'OFFLINE_SUITES',seal.OFFLINE_SUITES),patch.object(verifier,'OFFLINE_TEST_COUNT',seal.OFFLINE_TEST_COUNT),patch.object(verifier,'read',side_effect=lambda path,limit=0:path.read_bytes()),contextlib.redirect_stderr(io.StringIO()):
                result=verifier.offline_regressions(root)
            self.assertEqual(result['run'],27)
            self.assertTrue(result['passed'])
            self.assertEqual(result['errors'],0)

    def test_compose_and_freight_contracts_without_visual_claim_or_network(self):
        testfile=REPO/'portable-skills/tests/test_energy_programmatic_delivery_20261114.py'
        scope={'__name__':'local_programmatic_fixture','__file__':str(testfile)}
        exec(compile(testfile.read_bytes(),str(testfile),'exec'),scope)
        daily=scope['daily']
        with tempfile.TemporaryDirectory() as directory:
            temporary=Path(directory);project=temporary/'project';project.mkdir()
            scope['fixtures'].project_fixture(project)
            with patch.dict(os.environ,{'DEERFLOW_CONVERSATION_URL':verifier.SYNTHETIC_CONVERSATION_URL}),patch.object(daily,'png',side_effect=scope['mock_png']):
                report=daily.build(types.SimpleNamespace(workspace=str(project)))
                result=probe.qualify(daily,project,report,temporary)
                freight=probe.qualify_freight_render(daily,project,temporary)
            self.assertTrue(result['programmatic_acceptance_verified'])
            self.assertFalse(result['model_visual_review_used'])
            self.assertFalse(result['real_publication_proven'])
            self.assertTrue(freight['global_warning_hidden_in_png_only'])
            self.assertTrue(freight['non_png_quality_facts_preserved'])


if __name__=='__main__':
    unittest.main()
