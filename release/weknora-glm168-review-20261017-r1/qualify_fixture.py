"""Disposable Linux-only frozen-helper bootstrap and dry-run qualification."""
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import test_review as tests

m=tests.m
DOWNLOADS=Path('/downloads')
ROWS=(
('/var/lib/weknora-db-followup-tool-Em6tofN8/check.py','weknora-pipeline-db-followup-20261017-r1-verified/weknora-pipeline-db-followup-20261017-r1.py','c2f088e3d28d4250dfc7806519e384786682ebe0d5c410a09f98cfa5f9b738e1'),
('/var/lib/weknora-pipeline-deep-tool-GRY7Ffc9/observe.py','weknora-pipeline-deep-diagnostic-20261017-r1/weknora-pipeline-deep-diagnostic-20261017-r1.py','a4986f8045d8bac6951319469a5ba02d798ab432e73b273b8c093c4052f57a76'),
('/var/lib/weknora-glm104-diagnostic-tool-7whC6f8S/check.py','weknora-glm104-mineru-diagnostic-20261017-r1/weknora-glm104-mineru-diagnostic-20261017-r1.py','9756acd719f1fc78e84afbda621bdb0af32bbb15334d08ca0d197bb076dc5c74'),
('/var/lib/weknora-glm104-switch-tool-gZZZfsdn/switch.py','weknora-glm104-switch-20261017-r1/weknora-glm104-switch-20261017-r1.py','761d0f8de8ec6e6938216d1aba73c6c17dc69ae6f38475220740223f60079176'),
('/var/lib/weknora-glm104-review-tool-Fl7IvtXM/review.py','weknora-glm104-review-20261017-r1/weknora-glm104-review-20261017-r1.py','ecb124290028380637ae9bca1d6d348e58c0463901b960039ea62e10660ff410'),
('/var/lib/weknora-glm88-switch-tool-3IXKnz6L/switch.py','weknora-glm88-switch-20261017-r1/weknora-glm88-switch-20261017-r1.py','ebdf30fefd7e23038ad12fabe6adbcca96ccf3e3af3db388aa67f2ab2e69ec51'),
('/var/lib/weknora-glm88-review-tool-r0ivAWJl/review.py','weknora-glm88-review-20261017-r1/weknora-glm88-review-20261017-r1.py','b732b7433b30469f412f8ae0a7246afbb419506786688ba36bf4eac2a38a5f76'),
('/var/lib/weknora-glm-activate56-tool-sYzAva0R/activate.py','weknora-glm-activate56-20261017-r1/weknora-glm-activate56-20261017-r1.py','cf76e0dc1b1a9179ee3234562332847bfae3201be8070a4f0f042e981ee04580'),
('/var/lib/weknora-full-enrichment-tool-dunIcgBN/accept.py','weknora-full-enrichment-20261017-r1/weknora-full-enrichment-20261017-r1.py','a880111601819c65f813c806cbb28fb1adab42a931132b1e004a6886e85bc2c3'),
('/var/lib/weknora-tune88-tool-1z7lNY2B/tune.py','/legacy/weknora-tune88-20261016-r1.py','1ea5394a1814285df0cfc023a912b8d3cb0c8c4da0003238a84b04ca07fd8587'))


def main():
    assert sys.platform=='linux' and os.geteuid()==0 and Path('/.dockerenv').exists()
    for target,name,sha in ROWS:
        raw=(DOWNLOADS/name).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha,name
        p=Path(target);assert not p.exists();p.parent.mkdir(mode=0o700,parents=True);p.write_bytes(raw);p.chmod(0o600)
    # Network/process execution must be impossible while loading the exact frozen chain.
    with patch('subprocess.Popen',side_effect=AssertionError('unexpected subprocess during import')):
        c=m.bootstrap()
    assert c.sw.u.TARGET==m.CURRENT and c.e.TARGET_MODEL==128
    assert c.sw.target_env()[m.ENV_KEYS['enrichment']]=='64'
    assert c.e.TARGET_ENV[m.ENV_KEYS['enrichment']]=='48'  # never repoint old globals
    dep=tests.deployment()
    for n in m.TARGETS:
        target=m.proposal(dep['spec'],n)
        def reply(command,**kwargs):
            assert '--dry-run=server' in command and command[:3]==['patch','deployment','weknora-app']
            ops=json.loads(kwargs['data']);assert ops[0]['path']=='/metadata/uid'
            assert any(x.get('path')=='/spec' and x['op']=='test' for x in ops)
            return (0,json.dumps(dict(metadata=dep['metadata'],spec=target)).encode())
        with patch.object(c.r.sw.a.native,'call',side_effect=reply) as call:
            c.r.dry_run(dep,target);assert call.call_count==1
    assert c.e.TARGET_ENV[m.ENV_KEYS['enrichment']]=='48'
    print(json.dumps(dict(exact_frozen_helper_chain='PASS',helpers=len(ROWS),bootstrap_subprocesses=0,
        exact_server_dryrun_helper='PASS_MOCKED_API_ONLY',old_globals_unchanged=True,production_calls=0)))


if __name__=='__main__':main()
