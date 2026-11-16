"""Execute sealed-asset integrity and wrong-host guards on Python 3.7+."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
import types

REPO=Path(__file__).resolve().parents[3]
FILE=REPO/'release/energy-html-reader-and-skill-20261114-r1/install-energy-daily-html-links-20261114-r1.py'


def main():
    scope=runpy.run_path(str(FILE),run_name='sealed_bundle_qualification')
    assert set(scope['ASSETS'])=={'reader','skill'}
    for name,item in scope['ASSETS'].items():
        raw=base64.b64decode(item['base64'],validate=True)
        assert len(raw)==item['bytes'] and hashlib.sha256(raw).hexdigest()==item['sha256']
        module=types.ModuleType('_qualified_sealed_'+name);module.__file__=str(FILE);sys.modules[module.__name__]=module
        exec(compile(raw,'<qualified-'+name+'>','exec'),module.__dict__)
        if name=='skill':
            assets=module.load_assets()
            assert set(assets)=={'publisher','verifier','package','test_files'}
            assert module.CANDIDATE_VERSION=='2.1.5-portable' and module.OFFLINE_TEST_COUNT==34
        else:
            assert 'proxy_method GET;' in module.READER_SOURCE
            assert '油气与金融市场日报_' in module.READER_SOURCE
    original=sys.argv
    try:
        sys.argv=[str(FILE),'--install-daily-html-links-once']
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture):result=scope['main']()
        observed=json.loads(capture.getvalue());assert result==1 and observed['error_code']=='DATAX01_ROOT_REQUIRED'
        assert observed['provider_requests']==0 and observed['work_messages_sent']==0
    finally:sys.argv=original
    print(json.dumps({'schema':'energy-html-combined-local-qualification.v1','sealed_outer_assets':2,'sealed_skill_assets':4,
                      'wrong_host_zero_commands':True,'provider_requests':0,'production_access':False,'python':sys.version.split()[0]}))

if __name__=='__main__':main()
