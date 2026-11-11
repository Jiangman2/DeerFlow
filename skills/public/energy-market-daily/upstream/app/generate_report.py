"""Generate a mobile energy report. Local workbooks and verified news; optional Playwright PNG export."""
import argparse
import os
import calendar
import datetime as dt
import html
import json
import math
from pathlib import Path
from report_data import load_report
from report_renderer import render

ROOT = Path(__file__).resolve().parent


def previous_months(day, count):
    month = day.year * 12 + day.month - 1 - count
    year, month = divmod(month, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def export_png(path):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(**({'executable_path': os.environ['REPORT_BROWSER_EXECUTABLE']} if os.environ.get('REPORT_BROWSER_EXECUTABLE') else {'channel': os.environ.get('REPORT_BROWSER_CHANNEL','msedge')}), headless=True)
        page = browser.new_page(viewport={'width':390,'height':844}, device_scale_factor=2)
        page.goto(path.resolve().as_uri())
        page.evaluate('document.fonts.ready')
        page.screenshot(path=str(path.with_suffix('.png')), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow'
        for width in [320, 430]:
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), f'Overflow at {width}'
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='Publication date, YYYY-MM-DD')
    parser.add_argument('--source-dir', type=Path, default=ROOT/'data')
    parser.add_argument('--news', type=Path, help='Verified news JSON for publication date')
    parser.add_argument('--png', action='store_true', help='Export PNG using Playwright and Edge')
    args = parser.parse_args()
    publication = dt.date.fromisoformat(args.date)
    data = load_report(publication, previous_months(publication-dt.timedelta(days=1), 3), args.source_dir, args.news or args.source_dir/f'news_{publication.isoformat()}.json')
    out = ROOT / 'output'
    out.mkdir(exist_ok=True)
    path = out / f'油气与金融市场日报_{publication.isoformat()}.html'
    path.write_text(render(data), encoding='utf-8')
    path.with_suffix('.json').write_text(json.dumps(data,ensure_ascii=False,indent=2), encoding='utf-8')
    if args.png:
        export_png(path)
    print(path)


if __name__ == '__main__':
    main()
