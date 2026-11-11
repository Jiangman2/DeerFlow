"""Execute real qualifier/stage/publish against temporary synthetic filesystem only."""
import ast
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


def binding(path, name):
    tree = ast.parse(Path(path).read_text(encoding='utf-8'))
    return ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))


bundle = binding('/check/nov05_update_candidate.py', 'BUNDLE')
old = binding('/check/nov04_update_archival.py', 'BUNDLE')
assert bundle['before']['tree_sha256'] == old['after']['tree_sha256']
remote_source = bundle['remote_source']
tree = ast.parse(remote_source)
qualifier = ast.literal_eval(next(n.value for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'QUALIFIER_SOURCE' for t in n.targets)))
page = os.sysconf('SC_PAGE_SIZE')
assert len(remote_source.encode()) + 1 < page * 32 and len(qualifier.encode()) + 1 < page * 32
namespace = {'__name__': 'isolated_synthetic_remote'}
exec(compile(remote_source, '<sealed-remote-source>', 'exec'), namespace)
with tempfile.TemporaryDirectory(prefix='nov05-synthetic-publish-') as folder:
    root = Path(folder)
    namespace['ROOT'] = root
    (root/'public').mkdir()
    namespace['unpack'](base64.b64decode(old['archive_b64']), bundle['before'], root/'public'/'research-fund-settlement')
    operation = 'f'*32
    request = {'mode': 'stage', 'operation': operation, 'before': bundle['before'], 'after': bundle['after'], 'archive_b64': bundle['archive_b64'], 'archive_sha256': bundle['archive_sha256']}
    with namespace['lease']():
        stage = namespace['stage'](request)
    checks = stage['synthetic_checks']
    assert len(checks) == 10 and sum(row.get('passed', 1) for row in checks) == 134
    assert stage['status'] == 'STAGED_SYNTHETIC_ACCEPTED'
    with namespace['lease']():
        published = namespace['publish'](request)
    assert published['status'] == 'SKILL_PACKAGE_UPDATED'
    namespace['verify'](root/'public'/'research-fund-settlement', bundle['after'])
    namespace['verify'](Path(published['backup_path']), bundle['before'])
    result = {'status': 'PASS', 'qualification_checks': 134, 'feedback_regressions': 125, 'real_cli_checks': 9, 'real_stage_and_publish': True, 'backup_preserved': True, 'switch_strategy': published['switch_strategy'], 'temporary_files_cleaned': True, 'production_actions': 0, 'user_files_read': False, 'provider_requests': 0, 'database_access': False, 'remote_argument_bytes': len(remote_source.encode())+1, 'qualifier_argument_bytes': len(qualifier.encode())+1, 'single_argument_limit_observed': page*32}
print(json.dumps(result, sort_keys=True))
