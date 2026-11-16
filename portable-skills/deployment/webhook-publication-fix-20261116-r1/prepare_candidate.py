"""Initialize the isolated 2.1.8 candidate from the retained 2.1.7 source."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
old = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.7'
new = old.with_name('energy-market-daily-2.1.8')
assert not new.exists(), 'Do not overwrite an edited candidate'
shutil.copytree(old, new)
path = new / 'scripts/daily.py'
source = path.read_text(encoding='utf-8').replace("VERSION = '2.1.7-portable'", "VERSION = '2.1.8-portable'")
source = source.replace("(VERSION, '2.1.6-portable'", "(VERSION, '2.1.7-portable', '2.1.6-portable'")
path.write_text(source, encoding='utf-8')
path = new / 'scripts/delivery_text.py'
path.write_text(path.read_text(encoding='utf-8').replace("'2.1.7-portable'", "'2.1.7-portable', '2.1.8-portable'"), encoding='utf-8')
print('CANDIDATE_218_INITIALIZED_NOT_PUBLISHED')
