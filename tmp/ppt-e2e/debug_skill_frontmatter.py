from pathlib import Path
import re
p = Path('/app/skills/public/ppt-master/SKILL.md')
raw = p.read_bytes()
print('HEAD_BYTES=', raw[:6])
text = p.read_text(encoding='utf-8')
print('HEAD_REPR=', repr(text[:20]))
m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
print('MATCH=', bool(m))
print('LINE1=', repr(text.splitlines()[0] if text.splitlines() else ''))
