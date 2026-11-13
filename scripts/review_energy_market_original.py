"""CI-only review of the sealed portable Skill and archived original package.

The immutable original/ZIP is verified outside public discovery. Three scanner
matches are runtime credential reads, never literal values. The two subprocess
matches quote argv or invoke the fixed same-file worker with bounded timeout.
This does not change the platform's runtime scanner or another Skill's policy.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy/enterprise'))
import released_sync_source_20261110 as source

FIELDS = ('source','profile','rule_id','severity','path','line')
ALLOWED = {
    ('skillscan','deerflow','secret-env-assignment','error','scripts/common.py',70),
    ('skillscan','deerflow','python-subprocess','error','scripts/daily.py',47),
    ('skillscan','deerflow','secret-env-assignment','error','scripts/daily.py',203),
    ('skillscan','deerflow','python-subprocess','error','scripts/daily.py',210),
    ('skillscan','deerflow','secret-env-assignment','error','scripts/market.py',122),
}


def reviewed(facts):
    completeness = facts.get('completeness',{})
    if completeness.get('truncated') or completeness.get('not_assessed'):
        return False
    seen = []
    for finding in facts.get('findings',[]):
        if finding.get('severity') in ('warning','info'):
            continue
        identity = tuple(finding.get(k) for k in FIELDS)
        if identity not in ALLOWED:
            return False
        seen.append(identity)
    return set(seen) == ALLOWED and len(seen) == len(ALLOWED)


def main():
    # Full bytes, file set, ZIP contents, and production hashes must match first.
    paths = [p.relative_to(ROOT).as_posix() for p in (ROOT/source.ENERGY).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts]
    source.verify(lambda p:(ROOT/p).read_bytes(),paths)
    sys.path.insert(0,str(ROOT/'backend/packages/harness'))
    from deerflow.skills.review.analyzer import analyze_skill_package
    from deerflow.skills.review.readers import LocalDirectoryReader
    from deerflow.skills.review.cli import _print_text
    snapshot = LocalDirectoryReader(ROOT/source.ENERGY).read()
    facts = analyze_skill_package(snapshot)
    _print_text(facts)
    accepted = not snapshot.get('reader_errors') and reviewed(facts)
    print('[skill-review] Sealed portable sources, preserved original/ZIP, runtime credential reads and bounded worker: '+('REVIEWED' if accepted else 'FAILED'))
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
