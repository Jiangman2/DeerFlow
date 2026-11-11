"""CI-only review of the exact deployed, immutable upstream energy package.

The nested original instructions/ZIP are deliberate provenance, and the one
wrapper subprocess invokes the unchanged generator with bounded argv/time/output.
This does not change the platform's runtime scanner or another Skill's policy.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy/enterprise'))
import released_sync_source_20261110 as source

FIELDS = ('source','profile','rule_id','severity','path','line')
ALLOWED = {
    ('skillscan','deerflow','package-nested-archive','error','assets/energy-daily-v1.0.original.zip',None),
    ('skillscan','deerflow','python-subprocess','error','scripts/entry.py',172),
}
for path in ('upstream/agent/SKILL.md','upstream/provenance/original-skill/SKILL.md'):
    ALLOWED.add(('skillscan','deerflow','package-nested-skill-md','blocker',path,None))
    ALLOWED.add(('review-core','deerflow','structure.nested-skill-md','blocker',path,None))


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
    print('[skill-review] Exact deployed upstream/ZIP and bounded original subprocess: '+('REVIEWED' if accepted else 'FAILED'))
    return 0 if accepted else 1


if __name__ == '__main__':
    raise SystemExit(main())
