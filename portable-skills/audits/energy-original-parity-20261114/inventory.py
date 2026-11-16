"""Read-only original-archive/candidate provenance inventory; no execution/network."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ARCHIVE = Path('C:/Users/15285/Desktop/油气日报工程文件/energy-daily-v1.0.zip')
ORIGINAL = ROOT / '.codex-energy-daily-thin/skills/public/energy-market-daily/upstream'
CANDIDATE = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.2'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    raw = ARCHIVE.read_bytes()
    assert sha(raw) == 'eb66843bbe6170d86d33b65efa593be263e778275c6f7d491e1fd0c3c6d98f99'
    compared, differences, program = 0, [], []
    with zipfile.ZipFile(ARCHIVE) as zipped:
        for item in zipped.infolist():
            if item.is_dir():
                continue
            relative = item.filename.removeprefix('energy-daily-v1.0/')
            assert '..' not in Path(relative).parts and not Path(relative).is_absolute()
            if 'credential' in relative.casefold() or relative.endswith('.env'):
                raise ValueError('UNEXPECTED_PRIVATE_ARCHIVE_ENTRY')
            content = zipped.read(item)
            local = ORIGINAL / relative
            compared += 1
            if not local.is_file() or sha(local.read_bytes()) != sha(content):
                differences.append(relative)
            if relative.endswith(('.py', '.md')) and '/sources/' not in relative:
                program.append({'path': relative, 'bytes': len(content), 'sha256': sha(content)})
        original_renderer = zipped.read('energy-daily-v1.0/app/report_renderer.py')
    current_renderer = (CANDIDATE / 'scripts/report_renderer.py').read_bytes()
    reviewed_path = ROOT / 'portable-skills/deployment/delivery-context-skill-20261114-r1/REVIEWED-SOURCE.json'
    reviewed = json.loads(reviewed_path.read_bytes())
    current_mismatches = [relative for relative, record in reviewed['files'].items()
        if not (CANDIDATE / relative).is_file() or sha((CANDIDATE / relative).read_bytes()) != record['sha256']]
    history_seeds = sorted((CANDIDATE / 'assets/history').glob('*.json'))
    seed_mismatches = [path.name for path in history_seeds
        if not (ORIGINAL / 'app/sources' / path.name).is_file()
        or sha(path.read_bytes()) != sha((ORIGINAL / 'app/sources' / path.name).read_bytes())]
    old_nodes = {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(original_renderer).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    new_nodes = {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(current_renderer).body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    report = {'schema': 'energy-original-parity-inventory.v1', 'original_archive_bytes': len(raw), 'original_archive_sha256': sha(raw),
        'original_archive_files': compared, 'original_extracted_copy_mismatches': differences,
        'original_program_and_instruction_files': program,
        'current_candidate': '2.1.2', 'candidate_sources': [{'path': str(path.relative_to(CANDIDATE)).replace('\\', '/'), 'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())}
            for path in sorted(CANDIDATE.rglob('*')) if path.is_file() and path.suffix in ('.py', '.md')],
        'reviewed_source_sha256': sha(reviewed_path.read_bytes()), 'reviewed_source_mismatches': current_mismatches,
        'reviewed_source_version': reviewed['version'], 'history_seed_days': len(history_seeds), 'history_seed_mismatches': seed_mismatches,
        'renderer_original_sha256': sha(original_renderer), 'renderer_current_sha256': sha(current_renderer),
        'renderer_unchanged_functions': [name for name, body in old_nodes.items() if new_nodes.get(name) == body],
        'renderer_changed_functions': [name for name, body in old_nodes.items() if new_nodes.get(name) != body],
        'credentials_read': False, 'original_files_modified': False, 'provider_requests': 0, 'production_requests': 0}
    (OUT / 'INVENTORY.json').write_text(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('original_archive_files', 'original_extracted_copy_mismatches', 'reviewed_source_mismatches', 'history_seed_days', 'history_seed_mismatches', 'renderer_unchanged_functions', 'renderer_changed_functions', 'credentials_read', 'production_requests')}))


if __name__ == '__main__':
    main()
