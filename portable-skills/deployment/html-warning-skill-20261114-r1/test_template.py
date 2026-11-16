"""Reuse reviewed sealing and mounted-import checks for the HTML candidate."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
source = (HERE.parent / 'programmatic-skill-20261114-r1/test_template.py').read_text(encoding='utf-8')
source = source.replace('energy-market-daily-2.1.3', 'energy-market-daily-2.1.4')
source = source.replace('test_energy_programmatic_delivery_20261114.py', 'test_energy_html_delivery_20261114.py')
source = source.replace('test_energy_news_stage_20261114.py', 'test_energy_html_news_stage_20261114.py')
source = source.replace('global_warning_hidden_in_png_only', 'global_warning_hidden_in_html_and_png')
source = source.replace('install-energy-programmatic-skill-20261114-r1.py', 'install-energy-html-warning-skill-20261114-r1.py')
source = source.replace('915a4c765fc81e20b054bc77aa08ce9e919599d354a2aee7edc19db6637c1357',
                        'a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d')
exec(compile(source, str(HERE / 'test_template.py'), 'exec'), globals())
