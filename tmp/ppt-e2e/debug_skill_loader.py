from pathlib import Path
from deerflow.skills.parser import parse_skill_file
from deerflow.skills import load_skills

p = Path('/app/skills/public/ppt-master/SKILL.md')
base = Path('/app/skills/public')
s = parse_skill_file(p, 'public', p.parent.relative_to(base))
print('PARSE:', 'OK' if s else 'NONE')
if s:
    print('NAME=', s.name)
    print('DESC=', s.description)
all_skills = load_skills(enabled_only=False)
print('HAS_PPT_MASTER=', any(x.name == 'ppt-master' for x in all_skills))
print('MATCHES=', [x.name for x in all_skills if 'ppt' in x.name.lower() or 'master' in x.name.lower()])
print('TOTAL=', len(all_skills))
