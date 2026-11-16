"""Freeze the reviewed candidate and fixture inputs for deterministic packaging."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re

REPO=Path(__file__).resolve().parents[3]
HERE=REPO/'portable-skills/deployment/html-reader-skill-20261114-r1'
SKILL=REPO/'portable-skills/candidates/energy-market-daily-2.1.5'

def main():
    # Normalize only the new, unsealed templates; no historical file is touched.
    for path in HERE.glob('*.py'):
        source=path.read_text(encoding='utf-8').replace('html-reader-skill-skill','html-reader-skill')
        path.write_bytes(source.encode('utf-8'))
    path=REPO/'portable-skills/tests/test_energy_reader_delivery_20261114.py'
    source=path.read_text(encoding='utf-8').replace('DELIVERY_LINK_TEXT_LINK_CONFLICT','DELIVERY_TEXT_LINK_CONFLICT')
    source=source.replace("if __name__=='__main__': unittest.main()",'')
    source += "\nif __name__=='__main__': unittest.main()\n"
    path.write_bytes(source.encode('utf-8'))
    scope={'__name__':'reviewed_skill_sealer','__file__':str(HERE/'seal.py')}
    exec(compile((HERE/'seal.py').read_bytes(),str(HERE/'seal.py'),'exec'),scope)
    files={}
    for path in sorted(SKILL.rglob('*')):
        if path.is_file() and path.name not in ('MANIFEST.json','CANDIDATE.md'):
            assert '__pycache__' not in path.parts
            raw=path.read_bytes();files[path.relative_to(SKILL).as_posix()]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    fixtures={}
    for name in scope['FIXTURE_NAMES']:
        fixtures[name]=(REPO/'portable-skills/tests'/name).read_bytes()
    for name in scope['PRIOR_SOURCE_NAMES']:
        fixtures['published-2.1.2/scripts/'+name]=(REPO/'portable-skills/candidates/energy-market-daily-2.1.2/scripts'/name).read_bytes()
    review={'schema':'energy-programmatic-reviewed-source.v1','version':'2.1.5-portable','files':files,
            'offline_suites':scope['OFFLINE_SUITES'],'offline_test_count':34,
            'fixtures':{n:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for n,raw in fixtures.items()},
            'scope':'Authenticated HTML reader link only; acquisition, calculation, layout and runtime unchanged.',
            'production_executed':False}
    (HERE/'REVIEWED-SOURCE.json').write_text(json.dumps(review,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'files':len(files),'fixtures':len(fixtures),'offline_test_count':34}))

if __name__=='__main__':main()
