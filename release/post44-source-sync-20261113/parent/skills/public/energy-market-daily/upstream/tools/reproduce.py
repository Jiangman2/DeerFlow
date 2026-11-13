"""Offline frozen-input reproduction and exact image comparison."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from PIL import Image, ImageChops
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from report_renderer import render
from generate_report import export_png

def main():
    p=argparse.ArgumentParser();p.add_argument('--strict',action='store_true');args=p.parse_args()
    out=ROOT/'reproduced';out.mkdir(exist_ok=True)
    ref=ROOT/'reference/approved-2026-11-07'
    data=json.loads(ref.with_suffix('.json').read_text(encoding='utf-8'))
    html=render(data);path=out/'report.html';path.write_text(html,encoding='utf-8');export_png(path)
    a=Image.open(ref.with_suffix('.png')).convert('RGBA');b=Image.open(path.with_suffix('.png')).convert('RGBA')
    equal=a.size==b.size and a.tobytes()==b.tobytes()
    result=dict(html_equal=html==ref.with_suffix('.html').read_text(encoding='utf-8'),pixel_equal=equal,reference_size=list(a.size),rendered_size=list(b.size),png_sha256=hashlib.sha256(path.with_suffix('.png').read_bytes()).hexdigest())
    if a.size==b.size and not equal:ImageChops.difference(a,b).convert('RGB').save(out/'difference.png')
    (out/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if args.strict and not (equal and result['html_equal']):raise SystemExit(1)
if __name__=='__main__':main()
