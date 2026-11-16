"""Seal the exact qualified reader and the already sealed Skill-only installer."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
RELEASE=REPO/'release/energy-html-reader-and-skill-20261114-r1'
SKILL=REPO/'release/energy-daily-html-reader-skill-20261114-r1/install-energy-html-reader-skill-20261114-r1.py'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def main():
    reader=(HERE/'activate_reader.py').read_text(encoding='utf-8')
    helper=(HERE/'nginx_reader.py').read_text(encoding='utf-8')
    assert reader.count("READER_SOURCE = ''")==1
    reader=reader.replace("READER_SOURCE = ''",'READER_SOURCE = '+repr(helper)).encode()
    skill=SKILL.read_bytes();assert sha(skill)=='16355e886e6023bfd61bcadda8a8ab44bbc492533441a1f035e0cd21209c373e'
    raw_assets={'reader':reader,'skill':skill}
    assets={name:{'base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':sha(raw)} for name,raw in raw_assets.items()}
    bundle=(HERE/'bundle.py').read_text(encoding='utf-8');assert bundle.count('ASSETS = {}')==1
    bundle=bundle.replace('ASSETS = {}','ASSETS = '+repr(assets)).encode()
    for name,raw in [('helper',helper.encode()),('reader',reader),('bundle',bundle),('skill',skill)]:
        compile(raw,'<sealed-'+name+'>','exec');ast.parse(raw.decode(),feature_version=(3,7))
    filename='install-energy-daily-html-links-20261114-r1.py'
    files={filename:bundle,'activate-energy-html-reader-20261114-r1.py':reader}
    meta={'schema':'energy-html-reader-and-skill-package.v1','version':'2.1.5-portable','credentials_included':False,
          'production_executed':False,'gateway_rollout_requested':False,'frontend_rebuild_requested':False,
          'nginx_configuration_change':'One immutable ConfigMap clone plus one ConfigMap-name reference CAS patch',
          'reader_access':'Existing authenticated conversation artifact owner gate',
          'files':{n:{'bytes':len(raw),'sha256':sha(raw)} for n,raw in files.items()},
          'assets':{n:{'bytes':len(raw),'sha256':sha(raw)} for n,raw in raw_assets.items()},
          'sources':{p.name:{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())} for p in sorted(HERE.glob('*.py'))},
          'local_checks':{'skill_windows':34,'skill_linux_python310_uid1000':34,'nginx_parser':8,'activation_concurrency':5,
                          'real_nginx_http_fixed_upstream':15,'real_nginx_http_production_variable_upstream':15,
                          'mobile_390_inline_html_svg_and_csp':True},
          'business_acceptance_pending':True,'provider_requests':0,'work_messages_sent':0}
    files['SOURCE.json']=(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode()
    files['SHA256SUMS.txt']=''.join(sha(raw)+'  '+n+'\n' for n,raw in sorted(files.items())).encode()
    RELEASE.mkdir(exist_ok=True,parents=True)
    for name,raw in files.items():
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps({'release':str(RELEASE),**meta['files'][filename]}))

if __name__=='__main__':main()
