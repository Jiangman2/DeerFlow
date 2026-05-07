import subprocess
import sys
from pathlib import Path

project_name = sys.argv[1]
base = Path('/app/projects') / project_name
svg_output = base / 'svg_output'
notes_dir = base / 'notes'
exports_dir = base / 'exports'

for d in [svg_output, notes_dir, exports_dir]:
    d.mkdir(parents=True, exist_ok=True)

for i in range(1, 7):
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <rect width="1920" height="1080" fill="#0b1f3a"/>
  <text x="120" y="220" font-size="92" fill="#ffffff">PPT Master Test Page {i}</text>
  <text x="120" y="360" font-size="52" fill="#a9c7ff">Project: {project_name}</text>
  <text x="120" y="460" font-size="44" fill="#d6e4ff">Generated in /app/projects path</text>
</svg>'''
    (svg_output / f"{i:02d}_page.svg").write_text(svg, encoding='utf-8')

notes = []
for i in range(1, 7):
    notes.append(f"## Page {i}\n- test note for page {i}\n")
(notes_dir / 'total.md').write_text("\n".join(notes), encoding='utf-8')

venv_py = '/app/backend/.venv/bin/python'
scripts = '/app/skills/public/ppt-master/scripts'

def run(cmd):
    print('RUN:', ' '.join(cmd), flush=True)
    p = subprocess.run(cmd, text=True, capture_output=True)
    print(p.stdout, flush=True)
    if p.stderr:
        print(p.stderr, flush=True)
    if p.returncode != 0:
        raise SystemExit(p.returncode)

run([venv_py, f'{scripts}/project_manager.py', 'info', str(base)])
run([venv_py, f'{scripts}/total_md_split.py', str(base)])
run([venv_py, f'{scripts}/finalize_svg.py', str(base)])
run([venv_py, f'{scripts}/svg_to_pptx.py', str(base), '-s', 'final'])

pptx_files = sorted(exports_dir.glob('*.pptx'))
print('PROJECT_DIR=', base)
for f in pptx_files:
    print('PPTX=', f)
