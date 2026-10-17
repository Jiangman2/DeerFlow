"""Qualify and package a fresh one-shot recovery, not a new image build."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile

from package_enrichment_gateway_recovery import checked_tests
import production_node_preload as node
import preload_transfer_transport as transport

HERE = Path(__file__).resolve().parent
NAME = 'weknora-glm-preload-transfer-recovery-20261017-r1.py'
COMMON = ('verify_weknora_base', 'export_weknora_base', 'preload_native_candidate',
          'production_node_preload', 'preload_transfer_transport', 'recover_production_preload')
LOCAL = COMMON + ('transfer_base', 'gateway_enrichment_baseline', 'recover_enrichment_gateway', 'production_release_review')
TESTS = ['test_preload_transfer_recovery', 'test_production_release', 'test_transfer_base',
         'test_export_weknora_base', 'test_native_preload']


def package(output, prior_prepare, original_full):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('linux_root_qualification_required')
    if output.exists():
        raise RuntimeError('delivery_exists')
    prior = prior_prepare.read_bytes()
    if hashlib.sha256(prior).hexdigest() != 'ca68eb06010d2666b164bc2e2763a7fba4d05b046a20ab970b8201e86fe2bd24':
        raise RuntimeError('immutable_previous_tool_hash')
    # Load definitions only; no entry point, SSH, server command or model request.
    runpy.run_path(str(prior_prepare), run_name='previous_definitions_only')
    previous_remote = sys.modules['prepare_production_release'].REMOTE_CODE
    if hashlib.sha256(previous_remote.encode()).hexdigest() != '3462951988847fe4debb57e8460b92b8d38141c7e69e2e4eef71e3df6d39ff25':
        raise RuntimeError('previous_receiver_hash')
    tree = ast.parse(previous_remote)
    frozen_remote = ast.literal_eval(next(x.value for x in tree.body if isinstance(x, ast.Assign) and
                                         any(isinstance(t, ast.Name) and t.id == '_sources' for t in x.targets)))
    sources = {name:(HERE/(name+'.py')).read_text(encoding='utf-8') for name in LOCAL}
    for name, value in frozen_remote.items():
        if sources.get(name) != value:
            raise RuntimeError('previous_receiver_module_changed:'+name)
    for text in sources.values():
        ast.parse(text, feature_version=(3,7))
    remote_sources = {k:sources[k] for k in COMMON}
    remote = node.boot(remote_sources)
    remote += 'sys.modules["recover_production_preload"].REMOTE_SOURCES='+repr(remote_sources)+'\n'
    remote += 'try: sys.exit(sys.modules["recover_production_preload"].main(["--target-receive-once"]))\n'
    remote += 'except Exception as e:\n _r=sys.modules["recover_production_preload"]; print(__import__("json").dumps({"production_node_preload":"UNCONFIRMED","failed_check":_r.safe_error(e),"production_writes":0,"ready_for_rollout":False}),flush=True); sys.exit(1)\n'
    command = transport.ssh_command(remote, Path('/private/known_hosts'), '192.168.111.18', transport.marker())
    boot = node.boot(sources)
    boot += 'sys.modules["recover_production_preload"].REMOTE_CODE='+repr(remote)+'\n'
    boot += 'if __name__=="__main__":\n try: sys.exit(sys.modules["recover_production_preload"].main())\n except Exception as e:\n  _r=sys.modules["recover_production_preload"]; print(__import__("json").dumps({"production_preload_recovery":"UNCONFIRMED","failed_check":_r.safe_error(e),"production_writes":0,"ready_for_rollout":False})); sys.exit(1)\n'
    ast.parse(boot, feature_version=(3,7))
    ast.parse(remote, feature_version=(3,7))
    tests = checked_tests([sys.executable, '-B', '-m', 'unittest', '-v']+TESTS)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)/NAME
        p.write_text(boot, encoding='utf-8')
        checked_tests([sys.executable, '-I', '-B', str(p), '--help'])
        code = '''import runpy,sys,unittest
from pathlib import Path
runpy.run_path(sys.argv[1],run_name="packaged_definitions_only")
f=sys.modules['recover_enrichment_gateway']; f.OLD_TOOL=Path(sys.argv[2]); f.load_original()
suite=unittest.defaultTestLoader.loadTestsFromName('test_preload_transfer_recovery')
r=unittest.TextTestRunner(verbosity=2).run(suite)
assert r.wasSuccessful() and not r.skipped and r.testsRun==27
'''
        frozen = checked_tests([sys.executable, '-B', '-c', code, str(p), str(original_full.resolve())])
    output.mkdir(parents=True)
    (output/NAME).write_bytes(boot.encode())
    (output/'LOCAL-TESTS.txt').write_bytes(tests)
    (output/'PACKAGED-TESTS.txt').write_bytes(frozen)
    (output/'RUNBOOK.txt').write_bytes((HERE/'PRELOAD-TRANSFER-RECOVERY-RUNBOOK.txt').read_bytes())
    report = dict(script=NAME, sha256=hashlib.sha256(boot.encode()).hexdigest(), bytes=len(boot.encode()),
                  remote_command_bytes=len(command[-1].encode()), linux_tests='PASS_ZERO_SKIPS',
                  packaged_frozen_tests='27_PASS', python37_grammar='PASS', production_writes=0,
                  server_preload='NOT_EXECUTED_LOCALLY', ready_for_rollout=False,
                  source_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in sources.items()})
    (output/'VERIFICATION.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(tests.decode().split('----------------------------------------------------------------------')[-1].strip())
    print(json.dumps({k:report[k] for k in ('script','sha256','bytes','remote_command_bytes','packaged_frozen_tests')}))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    for name in ('output','prior-prepare','original-full'):
        p.add_argument('--'+name, type=Path, required=True)
    a=p.parse_args()
    package(a.output,a.prior_prepare,a.original_full)
