"""Batch public source acquisition; summaries remain an auditable single edit."""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import email.utils
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

from common import BEIJING, Client, Failure, evidence, now, save

STOCKS = [('海油 A 股', '600938.SH', 'sh600938', '元'), ('海油港股', '00883.HK', 'hk00883', '港元'),
          ('中石油', '601857.SH', 'sh601857', '元'), ('中石化', '600028.SH', 'sh600028', '元'),
          ('沪A指数', '000002.SH', 'sh000002', '点')]
FX = [('美元指数', 'DXY', 'DINIW', '点'), ('美元兑人民币', 'USD/CNY', 'fx_susdcny', '元/美元')]
FEEDS = [('Offshore Energy', 'https://www.offshore-energy.biz/feed/'),
         ('Oilprice', 'https://oilprice.com/rss/main'),
         ('Rigzone', 'https://www.rigzone.com/news/rss/rigzone_latest.aspx')]
NEWS_HOSTS = ('offshore-energy.biz', 'oilprice.com', 'rigzone.com')
ENERGY = re.compile(r'\boil\b|\bgas\b|\blng\b|petroleum|crude|opec|drilling|\brig\b|adnoc|qatarenergy|energean|vaalco|chevron|totalenergies|exxon|shell|油气|石油|天然气|原油', re.I)
MARKET = re.compile(r'price|market|opec|sanction|demand|supply|policy|tariff|attack|export hub|inventor|库存|油价|制裁|市场|政策|供需|需求|供应', re.I)


def jsonp(raw):
    body = raw.decode('utf-8-sig').strip()
    body = re.sub(r'^/\*.*?\*/\s*', '', body, count=1, flags=re.S)
    try:
        parsed = json.loads(body)
    except ValueError:
        match = re.fullmatch(r'(?:var\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*|[A-Za-z_][A-Za-z0-9_]*\s*\()(.*?)(?:;?)', body, re.S)
        if not match:
            raise Failure('FINANCE_RESPONSE_FORMAT') from None
        value = match[1].rstrip(';').strip()
        if value.startswith('(') and value.endswith(')'):
            value = value[1:-1]
        elif value.endswith(')') and not body.startswith('var '):
            value = value[:-1]
        parsed = json.loads(value)
    if not isinstance(parsed, (dict, list, str)):
        raise Failure('FINANCE_RESPONSE_FORMAT')
    return parsed


def financial_quote(name, code, unit, target, rows, source, url, proof):
    points = {}
    for date, close in rows:
        if not isinstance(date, str):
            continue
        try:
            day = dt.date.fromisoformat(date[:10]).isoformat()
            value = float(close)
            if not 0 < value < float('inf'):
                continue
            points[day] = value
        except (ValueError, TypeError):
            continue
    current = points.get(target)
    previous_dates = sorted(day for day in points if day < target)
    previous = previous_dates[-1] if previous_dates else None
    quote = dict(name=name, code=code, unit=unit, date=target, value=current, change=None, source=source, url=url,
                 basis='不复权日线收盘' if code not in ('DXY', 'USD/CNY') else '日线收盘', retrieved_at=proof['retrieved_at'], evidence=[proof['file']])
    if proof.get('sha256'):
        quote['evidence_sha256'] = proof['sha256']
    if current is None:
        quote.update(status='missing', missing_reason='目标日收盘值未在本次来源响应中核实；不据此推定休市')
    elif previous:
        quote.update(status='available', previous_date=previous, previous_value=points[previous],
                     change=(current / points[previous] - 1) * 100, change_method='calculated')
    else:
        quote.update(status='available', change_method='missing_previous')
    if code == 'USD/CNY':
        quote['decimals'] = 4
    return quote


def acquire_finance(project, publication, budget=60):
    target = (publication - dt.timedelta(days=1)).isoformat()
    deadline = time.monotonic() + budget

    def fetch(spec):
        name, code, symbol, unit = spec
        client = Client(deadline)
        url = 'https://gu.qq.com/' + symbol if code not in ('DXY', 'USD/CNY') else 'https://finance.sina.com.cn/money/forex/hq/' + ('USDCNY' if code == 'USD/CNY' else 'DINIW') + '.shtml'
        try:
            if code not in ('DXY', 'USD/CNY'):
                endpoint = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
                raw = client.get(endpoint, params={'param': symbol + ',day,,,320,'}, redirects=0)
                parsed = jsonp(raw)
                if not isinstance(parsed, dict) or str(parsed.get('code')) != '0':
                    raise Failure('FINANCE_RESPONSE_INVALID')
                series = parsed['data'][symbol]
                if 'day' not in series:
                    # Never silently substitute qfq/hfq adjusted closes.
                    raise Failure('UNADJUSTED_CLOSE_UNAVAILABLE')
                rows = [(row[0], row[2]) for row in series['day'] if isinstance(row, list) and len(row) >= 3]
                proof = evidence(project, 'finance-' + symbol + '.json', raw, endpoint, source='腾讯证券', series=symbol)
                return financial_quote(name, code, unit, target, rows, '腾讯证券', url, proof)
            endpoint = 'https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var%20_energy_daily=/NewForexService.getDayKLine'
            raw = client.get(endpoint, params={'symbol': symbol}, redirects=0)
            parsed = jsonp(raw)
            if isinstance(parsed, str):
                # Sina publishes dated CSV in a quoted JSONP string. Documented
                # source fixture: date,open,low,high,close,(optional extra).
                rows = [parts for record in parsed.split('|') if len(parts := record.split(',')) >= 5]
                rows = [(row[0], row[4]) for row in rows]
            elif isinstance(parsed, list):
                rows = [(row.get('day', row.get('date')), row.get('close')) for row in parsed if isinstance(row, dict)]
            else:
                raise Failure('FX_HISTORY_ENCODING_UNSUPPORTED')
            proof = evidence(project, 'finance-' + symbol + '.json', raw, endpoint, source='新浪财经', series=symbol)
            return financial_quote(name, code, unit, target, rows, '新浪财经', url, proof)
        except Exception as exc:
            reason = exc.code if isinstance(exc, Failure) else 'FINANCE_SOURCE_UNAVAILABLE'
            proof = evidence(project, 'finance-' + symbol + '-unavailable.json', json.dumps({'status': 'missing', 'reason': reason, 'target_date': target}).encode(), url)
            quote = financial_quote(name, code, unit, target, [], '腾讯证券' if code not in ('DXY', 'USD/CNY') else '新浪财经', url, proof)
            quote['missing_reason'] = '本次固定来源未能取得目标日可核实收盘数据：' + reason
            return quote

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        quotes = list(pool.map(fetch, STOCKS + FX))
    result = {'data_date': target, 'stocks': quotes[:5], 'fx': quotes[5:], 'retrieved_at': now().isoformat()}
    save(Path(project) / 'financial.json', result)
    return result


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip, self.active, self.parts, self.paragraphs, self.links = 0, False, [], [], []
        self.title_parts, self.in_title, self.published = [], False, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            self.skip += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'p' and not self.skip:
            self.active, self.parts = True, []
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'meta' and (attrs.get('property') in ('article:published_time', 'og:published_time') or attrs.get('name', '').lower() in ('pubdate', 'publishdate', 'publishdate', 'date')):
            self.published = attrs.get('content')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'footer', 'header'):
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.in_title = False
        if tag == 'p':
            text = ' '.join(' '.join(self.parts).split())
            if self.active and len(text) >= 45:
                self.paragraphs.append(text)
            self.active = False

    def handle_data(self, value):
        if self.in_title:
            self.title_parts.append(value)
        if self.active and not self.skip:
            self.parts.append(value)


def timestamp(value):
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        # A date-only item on edition day cannot establish a pre-cutoff time.
        if parsed.tzinfo is None:
            if len(value) <= 10:
                return None
            parsed = parsed.replace(tzinfo=BEIJING)
        return parsed
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else None
        except (ValueError, TypeError):
            return None


def feed_items(raw, source, cutoff):
    result = []
    root = ET.fromstring(raw)
    for item in root.findall('.//item')[:80]:
        title = item.findtext('title', '').strip()
        date = timestamp(item.findtext('pubDate'))
        url = item.findtext('link', '').strip()
        if not date or not cutoff - dt.timedelta(days=7) <= date <= cutoff or not url.startswith('https://'):
            continue
        if not ENERGY.search(title):
            continue
        result.append({'original_title': title, 'url': url, 'published_at': date.isoformat(), 'published': date.astimezone(BEIJING).date().isoformat(), 'source': source})
    return result


def acquire_news(project, publication, cutoff, budget=90):
    deadline, candidates, errors = time.monotonic() + budget, [], []
    for source, feed in FEEDS:
        try:
            raw = Client(deadline).get(feed, redirects=2, allowed_hosts=NEWS_HOSTS, compressed=True)
            evidence(project, 'feed-' + re.sub(r'\W', '-', source) + '.xml', raw, feed)
            candidates.extend(feed_items(raw, source, cutoff))
        except Exception as exc:
            errors.append({'source': source, 'error_code': exc.code if isinstance(exc, Failure) else 'NEWS_FEED_INVALID'})
    unique = {}
    for item in sorted(candidates, key=lambda x: x['published_at'], reverse=True):
        key = re.sub(r'\W', '', item['original_title']).casefold()
        if key not in unique:
            unique[key] = item
    # Discover at most 24 titles, fetch only the small batch needed for 3+5.
    # Extra margins allow one unavailable/duplicate story in either section.
    selected = list(unique.values())[:24]

    def fetch(pair):
        index, item = pair
        try:
            raw = Client(deadline, timeout=18).get(item['url'], redirects=2, limit=2 * 1024 * 1024, allowed_hosts=NEWS_HOSTS, compressed=True)
            page = Page()
            page.feed(raw.decode('utf-8', 'replace'))
            paragraphs = [p for p in page.paragraphs if len(p) < 5000]
            if not paragraphs:
                raise Failure('NEWS_ORIGINAL_BODY_UNAVAILABLE')
            proof = evidence(project, 'article-%02d.html' % index, raw, item['url'])
            return dict(item, id='article-%02d' % index, retrieved_at=proof['retrieved_at'], evidence=[proof['file']], evidence_sha256=proof['sha256'],
                        body='\n'.join(paragraphs[:14])[:10000], suggested_section='market_news' if MARKET.search(item['original_title']) else 'company_news')
        except Exception:
            return None

    market_indexes = [i for i, item in enumerate(selected) if MARKET.search(item['original_title'])]
    company_indexes = [i for i, item in enumerate(selected) if not MARKET.search(item['original_title'])]
    initial = sorted(set(market_indexes[:4] + company_indexes[:6]))
    remaining = [i for i in range(len(selected)) if i not in initial]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        articles = [item for item in pool.map(fetch, [(i, selected[i]) for i in initial]) if item]
        for offset in (0, 4):
            if sum(item['suggested_section'] == 'market_news' for item in articles) >= 3 and sum(item['suggested_section'] == 'company_news' for item in articles) >= 5:
                break
            extra = remaining[offset:offset + 4]
            articles.extend(item for item in pool.map(fetch, [(i, selected[i]) for i in extra]) if item)
    articles.sort(key=lambda item: item['published_at'], reverse=True)
    result = {'publication_date': publication.isoformat(), 'news_as_of': cutoff.isoformat(), 'retrieved_at': now().isoformat(), 'articles': articles,
              'source_failures': errors, 'automatic_fetch_is_not_editorial_verification': True}
    save(Path(project) / 'research.json', result)
    write_summary_tasks(project, result)
    return result


def write_summary_tasks(project, result):
    articles = result['articles']
    market_items = [item for item in articles if item['suggested_section'] == 'market_news'][:3]
    company_items = [item for item in articles if item['suggested_section'] == 'company_news'][:5]
    compact = {'publication_date': result['publication_date'], 'news_as_of': result['news_as_of'],
               'market_news': [dict(item, body=item['body'][:4500]) for item in market_items],
               'company_news': [dict(item, body=item['body'][:4500]) for item in company_items],
               'additional_candidates': [{'id': item['id'], 'title': item['original_title'], 'section': item['suggested_section']} for item in articles if item not in market_items + company_items]}
    save(Path(project) / 'summary-tasks.json', compact)
    return compact
