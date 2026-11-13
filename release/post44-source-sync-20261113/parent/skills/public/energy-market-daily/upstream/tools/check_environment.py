"""Inspect reproducibility prerequisites, without installing fonts or software."""
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
def main():
    baseline=json.loads((ROOT/'config/reference-environment.json').read_text(encoding='utf-8'))
    fonts=json.loads((ROOT/'config/font-manifest.json').read_text(encoding='utf-8'))
    directory=Path(os.environ.get('FONT_DIRECTORY',os.path.join(os.environ.get('WINDIR','C:/Windows'),'Fonts')))
    checks=[]
    for f in fonts:
        p=directory/f['filename'];actual=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        checks.append(dict(filename=f['filename'],expected=f['sha256'],actual=actual,match=actual==f['sha256']))
    packages={k:importlib.metadata.version(k) for k in baseline['packages']}
    with sync_playwright() as p:
        options={'executable_path':os.environ['REPORT_BROWSER_EXECUTABLE']} if os.environ.get('REPORT_BROWSER_EXECUTABLE') else {'channel':os.environ.get('REPORT_BROWSER_CHANNEL','msedge')}
        browser=p.chromium.launch(**options,headless=True);version=browser.version;browser.close()
    result=dict(platform=platform.platform(),python=sys.version,browser=version,browser_matches=version==baseline['browser_version'],fonts=checks,packages=packages,packages_match=packages==baseline['packages'])
    result['baseline_matches']=result['browser_matches'] and all(x['match'] for x in checks) and result['packages_match'] and sys.version==baseline['python'] and platform.platform()==baseline['platform']
    out=ROOT/'acceptance/environment-check.json';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print('MATCH' if result['baseline_matches'] else 'DIFFERENCES: review acceptance/environment-check.json before claiming pixel equivalence')
if __name__=='__main__':main()
