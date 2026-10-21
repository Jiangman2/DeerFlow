"""Record local, pure-helper qualification; never executes a runtime candidate."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
OLD = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/native-host-frontend-r1')


def pin(path):
    raw = path.read_bytes()
    return dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def main():
    source = (HERE/'host_frontend_stage.py').read_bytes()
    ast.parse(source, feature_version=(3, 7))
    assets_raw = (HERE/'ASSETS.json').read_bytes()
    assets = json.loads(assets_raw)
    template = (HERE/'stage_template.py').read_text(encoding='utf-8')
    expected = template.replace('__ASSET_SHA__',hashlib.sha256(assets_raw).hexdigest()).replace(
        '__ASSET_Z64__',base64.b64encode(zlib.compress(assets_raw,9)).decode()).encode()
    assert source == expected
    frozen = json.loads((OLD/'ARTIFACT-MANIFEST.json').read_bytes())
    for row in frozen['files']:
        actual = pin(OLD/row['path'])
        assert actual['bytes'] == row['bytes'] and actual['sha256'] == row['sha256']
    for name in ('BINDING.template.json','configmaps.template.json','host-pod.template.json','frontend-pod.template.json'):
        assert assets[name] == json.loads((OLD/name).read_bytes())
    spec = importlib.util.spec_from_file_location('host_frontend_stage',HERE/'host_frontend_stage.py')
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    report_path = HERE.parent/'native-preload-complete-evidence-r1/REPORT.json'
    report_raw = report_path.read_bytes()
    assert len(report_raw) == 24125 and hashlib.sha256(report_raw).hexdigest() == '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'
    report = json.loads(report_raw)
    inputs_path = HERE.parent/'native-preload-complete-evidence-r1/HOST-RUNTIME-INPUTS.json'
    assert json.loads(inputs_path.read_bytes()) == report['host_runtime_inputs']
    rendered = helper.render('a'*32,'portal-ppt-'+'a'*32,report['host_runtime_inputs'])
    for stage in rendered['stages']:
        helper.validate_stage_objects(stage,'a'*32,'portal-ppt-'+'a'*32)
    results = dict(
        schema='portal-ppt-native-host-frontend-helper-qualification.v1',
        status='PASS_LOCAL_PURE_HELPER_ONLY',
        files=[pin(HERE/name) for name in ('host_frontend_stage.py','stage_template.py','build_stage.py','test_stage.py','ASSETS.json','qualify_stage.py')],
        accepted_preload_report=pin(report_path), host_runtime_inputs=pin(inputs_path),
        frozen_manifest=pin(OLD/'ARTIFACT-MANIFEST.json'), frozen_files_verified=frozen['files'],
        exact_template_binding=True, adapters_and_harness_bytes_unchanged=True,
        python_syntax_floor='3.7',
        local_tests=dict(command='python -I -B release/portal-ppt-hotupdate-20261017/native-runtime-host-frontend-r1/test_stage.py',
                         tool_chunk='c64389',exit_code=0,tests=7,test_elapsed_seconds=0.046,
                         scope='SYNTHETIC_RENDERER_AND_RECEIPT_FIXTURES_NO_CANDIDATE_EXECUTION'),
        interface=dict(render='render(owner, namespace, host_runtime_inputs)',
                       request_check='validate_stage_objects(stage, owner, namespace)',
                       terminal='validate_terminal(role, raw_bytes, observed, namespace)',
                       namespace='portal-ppt-<same 32 lowercase hex owner>',
                       owner_label=helper.OWNER_LABEL,
                       roles=['host','frontend'],
                       terminal_containers=['host-probe','frontend-probe'],
                       frontend_image_pins_include_init_and_main=True),
        caller_owned_gates=['Fresh exact node UID/platform/readiness','Actual admitted manifest identity',
                           'Owned CNI network-isolation proof and unchanged deny-all policy',
                           'Terminal init and main container image IDs, restart counts and exits',
                           'Deadline and exact UID cleanup'],
        server_execution=False,kubernetes_access=False,model_access=False,production_mutation=False,
        native_arm64_qualified=False,ready_for_rollout=False)
    raw = (json.dumps(results,sort_keys=True,indent=2)+'\n').encode()
    (HERE/'QUALIFICATION.json').write_bytes(raw)
    print(json.dumps(dict(path=str(HERE/'QUALIFICATION.json'),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),status=results['status'])))


if __name__ == '__main__': main()
