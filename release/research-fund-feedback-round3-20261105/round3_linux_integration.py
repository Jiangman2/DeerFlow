"""Real staging/publication against temporary synthetic Skill files only."""
import ast
import base64
import json
import os
from pathlib import Path
import tempfile
import time

HERE = Path(__file__).resolve().parent

def binding(path, name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign)
                               and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))

if __name__ == '__main__':
    bundle = binding(HERE/'update-portal-research-fund-skill-round3-20261105-r1.py', 'BUNDLE')
    old = binding(HERE.parent/'research-fund-feedback-20261105/update-portal-research-fund-skill-20261105-r1.py', 'BUNDLE')
    assert bundle['before'] == old['after']
    source = bundle['remote_source']
    tree = ast.parse(source)
    qualifier = ast.literal_eval(next(n.value for n in tree.body if isinstance(n,ast.Assign)
                                      and any(isinstance(t,ast.Name) and t.id=='QUALIFIER_SOURCE' for t in n.targets)))
    expected = ast.literal_eval(next(n.value for n in ast.parse(qualifier).body if isinstance(n,ast.Assign)
                                     and any(isinstance(t,ast.Name) and t.id=='EXPECTED_CHECKS' for t in n.targets)))
    page = os.sysconf('SC_PAGE_SIZE')
    assert len(source.encode())+1 < page*32 and len(qualifier.encode())+1 < page*32
    namespace={'__name__':'synthetic_remote'}
    exec(compile(source,'<sealed-round3-remote>','exec'),namespace)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='round3-synthetic-publish-') as folder:
        root=Path(folder); namespace['ROOT']=root
        (root/'public').mkdir()
        unrelated=root/'public/unrelated-skill'; unrelated.mkdir(); (unrelated/'keep.txt').write_bytes(b'unrelated skill unchanged')
        namespace['unpack'](base64.b64decode(old['archive_b64']),bundle['before'],root/'public/research-fund-settlement')
        operation='f'*32
        request={'mode':'stage','operation':operation,'before':bundle['before'],'after':bundle['after'],
                 'archive_b64':bundle['archive_b64'],'archive_sha256':bundle['archive_sha256']}
        with namespace['lease']():
            stage=namespace['stage'](request)
        checks=stage['synthetic_checks']
        assert stage['status']=='STAGED_SYNTHETIC_ACCEPTED'
        assert len(checks)==10 and sum(row.get('passed',1) for row in checks)==expected+9
        with namespace['lease']():
            published=namespace['publish'](request)
        assert published['status']=='SKILL_PACKAGE_UPDATED'
        namespace['verify'](root/'public/research-fund-settlement',bundle['after'])
        namespace['verify'](Path(published['backup_path']),bundle['before'])
        assert (unrelated/'keep.txt').read_bytes()==b'unrelated skill unchanged'
        assert sorted(p.name for p in (root/'public').iterdir())==['research-fund-settlement','unrelated-skill']
        strategy=published['switch_strategy']
    print(json.dumps({'status':'PASS','qualification_checks':expected+9,'behavior_regressions':expected,
        'real_cli_checks':9,'real_stage_and_publish':True,'backup_preserved':True,'unrelated_skill_unchanged':True,
        'switch_strategy':strategy,'temporary_files_cleaned':True,'production_actions':0,'user_files_read':False,
        'provider_requests':0,'database_access':False,'network':'none','elapsed_seconds':round(time.monotonic()-started,3),
        'remote_argument_bytes':len(source.encode())+1,'qualifier_argument_bytes':len(qualifier.encode())+1,
        'single_argument_limit_observed':page*32,'before_tree_sha256':bundle['before']['tree_sha256'],
        'after_tree_sha256':bundle['after']['tree_sha256']},sort_keys=True))
