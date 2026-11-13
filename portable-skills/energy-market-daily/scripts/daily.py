"""Standalone Energy Daily CLI: prepare sources once, summarize once, build once."""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import BEIJING, Failure, credentials, digest, load, now, save
import market
import research
from report_renderer import render
from browser_runtime import observe_runtime, runtime_browser

VERSION = '2.0.2-portable'
# This release changes browser discovery only; prepared evidence stays valid.
PROJECT_VERSIONS = frozenset((VERSION, '2.0.1-portable', '2.0.0-portable'))


def require(ok, code, **details):
    if not ok:
        raise Failure(code, **details)


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def command(action, project, extra=None):
    parts = [sys.executable, str(Path(__file__).resolve()), action, '--workspace', str(project)]
    parts.extend(extra or [])
    return subprocess.list2cmdline(parts) if os.name == 'nt' else shlex.join(parts)


def browser_candidates():
    paths = []
    explicit = os.environ.get('REPORT_BROWSER_EXECUTABLE')
    if explicit:
        paths.append(explicit)
    caches = [os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), '/opt/ms-playwright']
    try:
        home = Path.home()
        caches.extend((home / '.cache/ms-playwright', home / 'AppData/Local/ms-playwright', home / 'Library/Caches/ms-playwright'))
    except (OSError, RuntimeError):
        pass
    for key in ('XDG_CACHE_HOME', 'LOCALAPPDATA'):
        if os.environ.get(key):
            caches.append(Path(os.environ[key]) / 'ms-playwright')
    for cache in filter(None, caches):
        if str(cache) == '0':  # Playwright's hermetic-install sentinel is not a directory.
            continue
        base = Path(cache)
        try:
            if not base.is_dir():
                continue
            for pattern in ('chromium_headless_shell-*/chrome-*/headless_shell*', 'chromium_headless_shell-*/chrome-*/chrome-headless-shell*'):
                paths.extend(str(p) for p in sorted(base.glob(pattern), reverse=True))
        except OSError:
            # Other users' caches can exist but be unsearchable. Discovery must
            # not prevent an accessible system browser from being attempted.
            continue
    paths.extend(filter(None, (shutil.which(name) for name in ('chromium', 'chromium-browser', 'google-chrome'))))
    paths.extend(('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Google/Chrome/Application/chrome.exe'))
    accepted, seen = [], set()
    for name in paths:
        try:
            path = Path(name)
            if not path.is_file() or not os.access(path, os.R_OK | os.X_OK):
                continue
            identity = os.path.normcase(str(path.resolve(strict=True)))
        except OSError:
            continue
        if identity not in seen:
            seen.add(identity)
            accepted.append(str(path))
    return accepted


def doctor(credential_path=None, workspace=None, edition_date=None, news_as_of=None):
    try:
        credentials(credential_path)
        configured = True
    except (Failure, OSError, ValueError, TypeError):
        configured = False
    observed = observe_runtime()
    python_ok = sys.version_info >= (3, 10)
    playwright_ok = importlib.util.find_spec('playwright') is not None
    binding = observed.get('operator_binding_present') is True
    applicable = observed.get('operator_binding_applicable', binding) is True
    runtime_ok = not (binding and applicable) or observed.get('operator_runtime_ready') is True
    ready = python_ok and playwright_ok and configured and runtime_ok
    errors = []
    if not runtime_ok:
        errors.append(observed.get('error_code') or 'BROWSER_RUNTIME_UNAVAILABLE')
    if not python_ok:
        errors.append('PYTHON_310_REQUIRED')
    if not playwright_ok:
        errors.append('PLAYWRIGHT_REQUIRED')
    if not configured:
        errors.append('CREDENTIALS_NOT_CONFIGURED')
    result = {'status': 'ENVIRONMENT_OBSERVED', 'python': platform.python_version(), 'architecture': platform.machine(),
            'python_supported': python_ok, 'playwright_installed': playwright_ok,
            'browser_candidate_count': len(browser_candidates()), 'credentials_configured': configured,
            'operator_browser_runtime_binding_present': binding, 'runtime_observation': observed,
            'proxy_configured': any(os.getenv(key) for key in ('ENERGY_DAILY_HTTP_PROXY', 'HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')),
            'credential_values_exported': False, 'external_requests': 0,
            'ready_for_report': ready, 'readiness_basis': 'READ_ONLY_PREREQUISITES',
            'next_action': 'prepare' if ready else 'stop_and_report_environment',
            'network_and_browser_launch_not_yet_tested': True}
    if errors:
        result.update(readiness_error_codes=errors, recovery_scope='operator', retry_allowed=False,
                      dependency_installation_can_repair=False)
    if ready and workspace:
        extra = []
        if credential_path:
            extra += ['--credentials', str(Path(credential_path).expanduser().resolve())]
        if edition_date:
            extra += ['--date', edition_date]
        if news_as_of:
            extra += ['--news-as-of', news_as_of]
        result['next_command'] = command('prepare', Path(workspace).expanduser().resolve(), extra)
    return result


def begin(args):
    try:
        publication = dt.date.fromisoformat(args.date) if args.date else now().date()
        cutoff = dt.datetime.fromisoformat(args.news_as_of) if args.news_as_of else min(now(), dt.datetime.combine(publication, dt.time.max, BEIJING))
    except ValueError:
        raise Failure('EDITION_DATE_OR_CUTOFF_INVALID') from None
    require(publication <= now().date(), 'FUTURE_EDITION_NOT_AVAILABLE')
    require(cutoff.tzinfo is not None and cutoff <= now() and cutoff.astimezone(BEIJING).date() <= publication, 'NEWS_CUTOFF_INVALID')
    project = Path(args.workspace).expanduser().resolve() if args.workspace else (Path.cwd() / ('energy-daily-' + publication.isoformat() + '-' + uuid.uuid4().hex[:8])).resolve()
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / 'PROJECT.json'
    if state_path.is_file():
        state = load(state_path)
        require(state.get('version') in PROJECT_VERSIONS, 'PROJECT_VERSION_MISMATCH')
        if args.date:
            require(state['publication_date'] == args.date, 'PROJECT_DATE_MISMATCH')
        if args.news_as_of:
            require(dt.datetime.fromisoformat(state['news_as_of']) == cutoff, 'PROJECT_CUTOFF_MISMATCH')
        if not (project / 'summary-tasks.json').is_file():
            require(digest((project / 'research.json').read_bytes()) == state['input_hashes']['research'], 'PREPARED_SOURCE_CHANGED')
            research.write_summary_tasks(project, load(project / 'research.json'))
        compact = load(project / 'summary-tasks.json')
        return {'status': 'SOURCES_ALREADY_PREPARED', 'project': str(project), 'publication_date': state['publication_date'],
                'data_date': state['data_date'], 'prepared_at': state.get('prepared_at'),
                'market_news_candidates': len(compact['market_news']), 'company_news_candidates': len(compact['company_news']),
                'research_file': str(project / 'summary-tasks.json'), 'news_file': str(project / 'news.json'),
                'next_command': command('build', project), 'collection_restarted': False,
                'quotes_not_refreshed': True, 'reuse_reason': 'PROJECT_ALREADY_PREPARED'}
    started = time.monotonic()
    print(json.dumps({'phase': 'ACQUIRE_PARALLEL', 'stages': ['market', 'finance', 'news']}), file=sys.stderr, flush=True)
    # No shell state, server deployment, cluster service or platform SDK needed.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {name: pool.submit(acquisition_job, name, project, publication, cutoff, args) for name in ('market', 'finance', 'news')}
        names = {future: name for name, future in tasks.items()}
        results = {}
        for future in concurrent.futures.as_completed(names):
            name = names[future]
            results[name] = future.result()
            print(json.dumps({'phase': 'SOURCE_STAGE_FINISHED', 'stage': name}), file=sys.stderr, flush=True)
    state = {'version': VERSION, 'publication_date': publication.isoformat(), 'data_date': (publication - dt.timedelta(days=1)).isoformat(),
             'news_as_of': cutoff.isoformat(), 'prepared_at': now().isoformat(), 'prepare_seconds': round(time.monotonic() - started, 3),
             'input_hashes': {name: digest((project / file).read_bytes()) for name, file in (('market', 'market.json'), ('financial', 'financial.json'), ('research', 'research.json'))}}
    # A compact template prevents the model from re-authoring source identities.
    articles = results['news']['articles']
    compact = load(project / 'summary-tasks.json')
    template = {'publication_date': publication.isoformat(), 'news_as_of': cutoff.isoformat(), 'market_news': [], 'company_news': []}
    save(project / 'news.json', template)
    save(state_path, state)
    enough = len(compact['market_news']) >= 3 and len(compact['company_news']) >= 5
    return {'status': 'READY_FOR_NEWS_SUMMARIES' if enough else 'NEWS_SUPPLEMENT_REQUIRED', 'project': str(project), **state,
            'market_news_candidates': len(compact['market_news']), 'company_news_candidates': len(compact['company_news']),
            'market_data_complete': results['market']['market_complete'],
            'financial_quotes_available': sum(q['value'] is not None for key in ('stocks', 'fx') for q in results['finance'][key]),
            'news_articles_with_original_pages': len(articles), 'research_file': str(project / 'summary-tasks.json'),
            'news_file': str(project / 'news.json'), 'next_command': command('build', project),
            'credential_values_exported': False, 'collection_restarted': False}


def acquisition_job(task, project, publication, cutoff, args):
    budget = {'market': args.market_budget, 'finance': args.finance_budget, 'news': args.news_budget}[task]
    argv = [sys.executable, str(Path(__file__).resolve()), '_acquire', '--task', task, '--workspace', str(project),
            '--date', publication.isoformat(), '--news-as-of', cutoff.isoformat(), '--market-budget', str(args.market_budget),
            '--finance-budget', str(args.finance_budget), '--news-budget', str(args.news_budget)]
    if args.credentials:
        argv += ['--credentials', args.credentials]
    if args.cache:
        argv += ['--cache', args.cache]
    try:
        # A parent-enforced subprocess timeout also bounds DNS/slow-drip reads.
        # Worker programs spawn no further children and publish files atomically.
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=budget + 15, check=False)
        if result.returncode or len(result.stdout) > 16384 or len(result.stderr) > 65536:
            raise Failure('ACQUISITION_WORKER_FAILED')
        require(json.loads(result.stdout).get('status') == 'ACQUISITION_FINISHED', 'ACQUISITION_WORKER_FAILED')
        return load(project / {'market': 'market.json', 'finance': 'financial.json', 'news': 'research.json'}[task])
    except Exception:
        target = (publication - dt.timedelta(days=1)).isoformat()
        if task == 'market':
            value = market.build(market.previous_months(dt.date.fromisoformat(target)).isoformat(), target, [], {target: 'ACQUISITION_DEADLINE_OR_WORKER_FAILURE'})
            save(project / 'market.json', value)
        elif task == 'finance':
            quotes = []
            for name, code, symbol, unit in research.STOCKS + research.FX:
                proof = {'retrieved_at': now().isoformat(), 'file': 'sources/financial-worker-failure.json'}
                quotes.append(research.financial_quote(name, code, unit, target, [], '固定来源未取得', 'https://gu.qq.com/' + symbol, proof))
            save(project / 'sources/financial-worker-failure.json', {'status': 'missing', 'reason': 'ACQUISITION_DEADLINE_OR_WORKER_FAILURE'})
            value = {'data_date': target, 'stocks': quotes[:5], 'fx': quotes[5:]}
            save(project / 'financial.json', value)
        else:
            value = {'publication_date': publication.isoformat(), 'news_as_of': cutoff.isoformat(), 'articles': [],
                     'source_failures': [{'error_code': 'ACQUISITION_DEADLINE_OR_WORKER_FAILURE'}]}
            save(project / 'research.json', value)
            research.write_summary_tasks(project, value)
        return value


def worker(args):
    project, publication = Path(args.workspace).resolve(), dt.date.fromisoformat(args.date)
    if args.task == 'market':
        market.acquire(project, publication, args.credentials, args.cache, args.market_budget)
    elif args.task == 'finance':
        research.acquire_finance(project, publication, args.finance_budget)
    elif args.task == 'news':
        research.acquire_news(project, publication, dt.datetime.fromisoformat(args.news_as_of), args.news_budget)
    else:
        raise Failure('ACQUISITION_TASK_INVALID')
    return {'status': 'ACQUISITION_FINISHED', 'task': args.task}


def select_news(args):
    project = Path(args.workspace).resolve()
    sources = load(project / 'research.json')
    catalog = {article['id']: article for article in sources['articles']}
    if not args.market_ids and not args.company_ids:
        chosen = research.write_summary_tasks(project, sources)
    else:
        selected = {'market_news': (args.market_ids or '').split(','), 'company_news': (args.company_ids or '').split(',')}
        require(3 <= len(selected['market_news']) <= 5 and 5 <= len(selected['company_news']) <= 8, 'NEWS_SELECTION_COUNT_INVALID')
        ids = selected['market_news'] + selected['company_news']
        require(len(ids) == len(set(ids)) and all(key in catalog for key in ids), 'NEWS_SELECTION_ID_INVALID')
        chosen = {'publication_date': sources['publication_date'], 'news_as_of': sources['news_as_of'],
                  **{section: [dict(catalog[key], body=catalog[key]['body'][:4500]) for key in values] for section, values in selected.items()}}
        save(project / 'summary-tasks.json', chosen)
    return {'status': 'SUMMARY_BATCH_READY', 'file': str(project / 'summary-tasks.json'),
            'market_count': len(chosen['market_news']), 'company_count': len(chosen['company_news'])}


def import_news(args):
    project = Path(args.workspace).resolve()
    state, catalog = load(project / 'PROJECT.json'), load(project / 'research.json')
    additions = load(args.sources)
    require(isinstance(additions, list) and 0 < len(additions) <= 10, 'NEWS_ADDITIONS_COUNT_INVALID')
    cutoff = dt.datetime.fromisoformat(state['news_as_of'])
    existing = {item['url'] for item in catalog['articles']}
    added = 0
    for item in additions:
        require(isinstance(item.get('url'), str) and re.match(r'^https://[^/@\s]+/', item['url']), 'NEWS_SOURCE_URL_INVALID')
        require(isinstance(item.get('source'), str) and 1 <= len(item['source']) <= 100, 'NEWS_SOURCE_NAME_REQUIRED')
        if item['url'] in existing:
            continue
        published = research.timestamp(item.get('published_at'))
        require(published is not None and cutoff - dt.timedelta(days=7) <= published <= cutoff, 'NEWS_TIME_INVALID')
        path = (project / item.get('evidence', '')).resolve()
        require(project in path.parents and path.suffix.lower() in ('.html', '.txt', '.md') and path.is_file() and path.stat().st_size <= 2 * 1024 * 1024, 'EVIDENCE_PATH_INVALID')
        raw = path.read_bytes()
        page = research.Page()
        body = raw.decode('utf-8', 'replace')
        page.feed(body)
        if path.suffix.lower() == '.html':
            paragraphs = page.paragraphs
        else:
            paragraphs = [paragraph.strip() for paragraph in re.split(r'\n\s*\n', body) if len(paragraph.strip()) >= 45]
        require(bool(paragraphs), 'NEWS_ORIGINAL_BODY_UNAVAILABLE')
        title = item.get('original_title') or ' '.join(page.title_parts)
        require(isinstance(title, str) and len(title) >= 8, 'NEWS_ORIGINAL_TITLE_REQUIRED')
        if page.published:
            parsed = research.timestamp(page.published)
            require(parsed is not None and parsed.astimezone(BEIJING).date() == published.astimezone(BEIJING).date() and parsed <= cutoff, 'NEWS_PAGE_DATE_CONFLICT')
        catalog['articles'].append({'id': 'extra-' + digest(item['url'].encode())[:12], 'url': item['url'], 'source': item['source'],
                                    'original_title': title, 'published_at': published.isoformat(), 'published': published.astimezone(BEIJING).date().isoformat(),
                                    'retrieved_at': now().isoformat(), 'evidence': [str(path.relative_to(project)).replace('\\', '/')], 'evidence_sha256': digest(raw),
                                    'body': '\n'.join(paragraphs[:14])[:10000], 'suggested_section': item.get('section') if item.get('section') in ('market_news', 'company_news') else 'company_news',
                                    'editor_verified_source_date_required': not bool(page.published)})
        existing.add(item['url'])
        added += 1
    save(project / 'research.json', catalog)
    state['input_hashes']['research'] = digest((project / 'research.json').read_bytes())
    save(project / 'PROJECT.json', state)
    research.write_summary_tasks(project, catalog)
    return {'status': 'VERIFIED_SOURCE_EVIDENCE_IMPORTED', 'added': added, 'article_count': len(catalog['articles']), 'file': str(project / 'summary-tasks.json')}


def evidence_valid(project, quote):
    files = quote.get('evidence', [])
    files = [files] if isinstance(files, str) else files
    require(isinstance(files, list) and bool(files), 'EVIDENCE_REQUIRED')
    for file in files:
        path = (project / file).resolve()
        require(project in path.parents and path.is_file(), 'EVIDENCE_PATH_INVALID')
        if quote.get('evidence_sha256'):
            require(digest(path.read_bytes()) == quote['evidence_sha256'], 'EVIDENCE_CHANGED')
    require(isinstance(quote.get('retrieved_at'), str), 'RETRIEVAL_TIME_REQUIRED')
    try:
        require(dt.datetime.fromisoformat(quote['retrieved_at']).tzinfo is not None, 'RETRIEVAL_TIME_INVALID')
    except ValueError:
        raise Failure('RETRIEVAL_TIME_INVALID') from None


def snapshot(project):
    state = load(project / 'PROJECT.json')
    require(state.get('version') in PROJECT_VERSIONS, 'PROJECT_VERSION_MISMATCH')
    for name, file in (('market', 'market.json'), ('financial', 'financial.json'), ('research', 'research.json')):
        require(digest((project / file).read_bytes()) == state['input_hashes'][name], 'PREPARED_SOURCE_CHANGED', input=name)
    energy, finance, sources, news = (load(project / file) for file in ('market.json', 'financial.json', 'research.json', 'news.json'))
    publication, target = state['publication_date'], state['data_date']
    require(energy['data_date'] == finance['data_date'] == target, 'SNAPSHOT_DATE_MISMATCH')
    for kind, codes in (('oil', list(market.SPECS)[:3]), ('gas', list(market.SPECS)[3:]), ('freight', market.FREIGHT)):
        require([q['code'] for q in energy[kind]] == codes, 'MARKET_IDENTITY_MISMATCH')
        for quote in energy[kind]:
            require(quote['date'] == target and (numeric(quote['value']) or quote['value'] is None and quote['status'] in ('non_trading', 'missing')), 'MARKET_QUOTE_INVALID')
            if quote['change'] is not None:
                require(numeric(quote.get('previous_value')) and quote.get('previous_date', '9999') < target and
                        math.isclose(quote['change'], (quote['value'] / quote['previous_value'] - 1) * 100, abs_tol=1e-9), 'MARKET_CHANGE_INVALID')
    for kind, specs in (('stocks', research.STOCKS), ('fx', research.FX)):
        require(len(finance[kind]) == len(specs), 'FINANCIAL_SET_INVALID')
        for quote, (_, code, symbol, unit) in zip(finance[kind], specs):
            require(quote['code'] in (code, symbol) and quote['unit'] == unit and quote['date'] == target, 'FINANCIAL_IDENTITY_INVALID')
            evidence_valid(project, quote)
            if quote['value'] is None:
                require(quote['status'] in ('missing', 'non_trading') and quote['change'] is None and quote.get('missing_reason'), 'FINANCIAL_MISSING_UNEXPLAINED')
            else:
                require(numeric(quote['value']), 'FINANCIAL_NUMBER_INVALID')
                if quote['change'] is None:
                    require(quote.get('change_method') == 'missing_previous', 'FINANCIAL_CHANGE_UNEXPLAINED')
                else:
                    require(numeric(quote.get('previous_value')) and quote.get('previous_date', '9999') < target and
                            math.isclose(quote['change'], (quote['value'] / quote['previous_value'] - 1) * 100, abs_tol=1e-9), 'FINANCIAL_CHANGE_INVALID')
    require(news.get('publication_date') == publication and news.get('news_as_of') == state['news_as_of'], 'NEWS_EDITION_MISMATCH')
    require(3 <= len(news.get('market_news', [])) <= 5 and 5 <= len(news.get('company_news', [])) <= 8, 'NEWS_COUNT_INSUFFICIENT',
            market_count=len(news.get('market_news', [])), company_count=len(news.get('company_news', [])))
    articles = {item['id']: item for item in sources['articles']}
    used = set()
    accepted = {'market_news': [], 'company_news': []}
    for section in accepted:
        for item in news[section]:
            article = articles.get(item.get('article_id'))
            require(article is not None and article['id'] not in used, 'NEWS_SOURCE_OR_DUPLICATE_INVALID')
            used.add(article['id'])
            require(isinstance(item.get('title'), str) and 4 <= len(item['title']) <= 100 and isinstance(item.get('text'), str) and 30 <= len(item['text']) <= 1500, 'NEWS_SUMMARY_INVALID')
            # Source URL/date/evidence are copied from the collected original,
            # never accepted from model-authored fields.
            evidence_valid(project, article)
            require(digest((project / article['evidence'][0]).read_bytes()) == article['evidence_sha256'], 'NEWS_EVIDENCE_CHANGED')
            published = research.timestamp(article['published_at'])
            cutoff = dt.datetime.fromisoformat(state['news_as_of'])
            require(published is not None and cutoff - dt.timedelta(days=7) <= published <= cutoff, 'NEWS_TIME_INVALID')
            accepted[section].append({key: article[key] for key in ('url', 'source', 'published', 'published_at', 'retrieved_at', 'evidence', 'original_title')} | {'title': item['title'], 'text': item['text']})
    financial_missing = sum(q.get('status') == 'missing' for kind in ('stocks', 'fx') for q in finance[kind])
    holiday_count = sum(q.get('status') == 'non_trading' for kind in ('oil', 'gas', 'freight') for q in energy[kind])
    quality = '数据不完整' if not energy['market_complete'] else '金融缺值标注版' if financial_missing else '休市标注版' if holiday_count else '完整报告'
    return dict(publication_date=publication, data_date=target, oil=energy['oil'], gas=energy['gas'], freight=energy['freight'],
                stocks=finance['stocks'], fx=finance['fx'], history=energy['history'], **accepted, news_as_of=state['news_as_of'],
                gas_source='金凯讯', api_energy=True, formal_report_ready=energy['market_complete'],
                quality=quality, financial_missing_count=financial_missing, supplier_holiday_count=holiday_count, acquisition_failures=energy['acquisition_failures'],
                sources=[{'file': name, 'sha256': digest((project / name).read_bytes())} for name in ('market.json', 'financial.json', 'research.json', 'news.json')])


def png(path):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Failure('PLAYWRIGHT_REQUIRED') from None
    with runtime_browser() as managed, sync_playwright() as tool:
        found = browser_candidates()
        # An explicit operator choice or accessible headless shell avoids a
        # known-inaccessible default cache. Keep the default as a fallback for
        # hermetic Playwright installations not exposed through these paths.
        preferred = [p for p in found if p == os.environ.get('REPORT_BROWSER_EXECUTABLE') or 'headless_shell' in p or 'chrome-headless-shell' in p]
        candidates = ([managed] if managed else []) + preferred + [None] + [p for p in found if p not in preferred]
        errors, stages = [], []
        for executable in candidates[:8]:
            browser = None
            stage = 'launch'
            try:
                browser = tool.chromium.launch(headless=True, timeout=12000, **({'executable_path': executable} if executable else {}))
                stage = 'new_page'
                page = browser.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
                # Rendering is self-contained and must not load source pages.
                page.route('http://**/*', lambda route: route.abort())
                page.route('https://**/*', lambda route: route.abort())
                stage = 'load_local_report'
                page.goto(path.resolve().as_uri(), wait_until='load', timeout=15000)
                page.evaluate('document.fonts.ready')
                for width in (320, 390, 430):
                    page.set_viewport_size({'width': width, 'height': 844})
                    require(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'REPORT_HORIZONTAL_OVERFLOW', width=width)
                page.set_viewport_size({'width': 390, 'height': 844})
                stage = 'screenshot'
                page.screenshot(path=str(path.with_suffix('.png')), full_page=True, timeout=20000)
                raw = path.with_suffix('.png').read_bytes()
                require(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) > 1000, 'PNG_INVALID')
                width, height = struct.unpack('>II', raw[16:24])
                require(width == 780 and height >= 1000, 'PNG_DIMENSIONS_INVALID')
                return {'width': width, 'height': height, 'browser_fallback_count': len(errors)}
            except Exception as exc:
                errors.append(exc.code if isinstance(exc, Failure) else type(exc).__name__)
                stages.append(stage)
            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass  # A crashed process must not mask the next candidate.
        raise Failure('NO_USABLE_BROWSER', attempts=len(errors), categories=sorted(set(errors)), failed_stages=stages,
                      prepared_sources_preserved=True, recovery='PROVISION_ACCESSIBLE_BROWSER_THEN_RETRY_SAME_BUILD')


def reply_text(data):
    sections = ['油气与金融市场日报', '发布日期：' + data['publication_date'] + '；行情日期：' + data['data_date'], '报告状态：' + data['quality']]
    for title, kind in (('原油市场', 'oil'), ('天然气市场', 'gas'), ('航运运费', 'freight'), ('石油公司与股市', 'stocks'), ('汇率与美元', 'fx')):
        sections += ['', title]
        for quote in data[kind]:
            value = '—' if quote['value'] is None else format(quote['value'], ',.' + str(quote.get('decimals', 2)) + 'f')
            change = '休市' if quote.get('status') == 'non_trading' else quote.get('missing_reason', '前值待补') if quote['change'] is None else '%+.2f%%' % quote['change']
            sections.append('%s：%s %s（%s）' % (quote['name'], value, quote['unit'], change))
    for title, key in (('油气市场重要新闻', 'market_news'), ('石油公司动态', 'company_news')):
        sections += ['', title]
        for index, item in enumerate(data[key], 1):
            label = re.sub(r'([\\\[\]*_`])', r'\\\1', item['title'])
            target = item['url'].replace('(', '%28').replace(')', '%29')
            sections.append('%d. [%s](%s)\n%s\n来源：%s %s' % (index, label, target, item['text'], item['source'], item['published']))
    return '\n'.join(sections) + '\n'


def build(args):
    project = Path(args.workspace).expanduser().resolve()
    data = snapshot(project)
    if (project / 'RESULT.json').is_file():
        previous = load(project / 'RESULT.json')
        try:
            validate_result(previous, project)
            existing_json = next(Path(item['path']) for item in previous['files'] if item['path'].endswith('.json'))
            if existing_json.is_file() and load(existing_json) == data:
                return dict(previous, reused=True, artifacts_ready=True, publication_allowed=previous['status'] == 'REPORT_ACCEPTED')
        except (Failure, KeyError, ValueError, OSError):
            pass
    out = project / 'outputs'
    require(not out.is_symlink() and out.resolve().parent == project, 'OUTPUT_DIRECTORY_INVALID')
    basename = '油气与金融市场日报_' + data['publication_date']
    generation = 'generation-' + uuid.uuid4().hex
    # No newly rendered partial files enter the public outputs tree. A failed
    # PNG leaves the previous accepted generation and receipt byte-for-byte intact.
    with tempfile.TemporaryDirectory(prefix='.energy-build-', dir=project) as temporary:
        staged = Path(temporary)
        html_path = staged / (basename + '.html')
        html_path.write_text(render(data), encoding='utf-8')
        save(staged / (basename + '.json'), data)
        (staged / (basename + '.txt')).write_text(reply_text(data), encoding='utf-8')
        started = time.monotonic()
        image = png(html_path)
        staged_files = [staged / (basename + extension) for extension in ('.png', '.html', '.json', '.txt')]
        require(all(path.is_file() for path in staged_files), 'GENERATED_FILE_SET_INVALID')
        raw_image = staged_files[0].read_bytes()
        require(len(raw_image) > 1000 and raw_image.startswith(b'\x89PNG\r\n\x1a\n')
                and struct.unpack('>II', raw_image[16:24])[0] == 780, 'PNG_INVALID')
        hashes = {path.name: {'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes())} for path in staged_files}
        destination = out / generation
        out.mkdir(exist_ok=True)
        require(not destination.exists(), 'OUTPUT_GENERATION_EXISTS')
        os.rename(staged, destination)
    files = [destination / (basename + extension) for extension in ('.png', '.html', '.json', '.txt')]
    text_path = destination / (basename + '.txt')
    receipt = {'status': 'REPORT_GENERATED_VISUAL_REVIEW_REQUIRED', 'version': VERSION, 'project': str(project),
               'outputs_generation': generation,
               'artifacts_ready': True, 'publication_allowed': False,
               'publication_date': data['publication_date'], 'data_date': data['data_date'], 'quality': data['quality'],
               'formal_report_ready': data['formal_report_ready'], 'files': [{'path': str(file), **hashes[file.name]} for file in files],
               'reply_text_file': str(text_path), 'png': image, 'render_seconds': round(time.monotonic() - started, 3),
               'next_command': command('accept', project), 'credential_values_exported': False}
    save(project / 'RESULT.json', receipt)
    return receipt


def validate_result(receipt, project):
    require(receipt.get('version') in PROJECT_VERSIONS and receipt.get('status') in ('REPORT_GENERATED_VISUAL_REVIEW_REQUIRED', 'REPORT_ACCEPTED')
            and Path(receipt.get('project', '')).resolve() == project, 'RESULT_IDENTITY_INVALID')
    publication = load(project / 'PROJECT.json')['publication_date']
    destination = project / 'outputs'
    generation = receipt.get('outputs_generation')
    if generation is not None:
        require(isinstance(generation, str) and re.fullmatch(r'generation-[0-9a-f]{32}', generation), 'RESULT_IDENTITY_INVALID')
        destination = destination / generation
    expected = {str((destination / ('油气与金融市场日报_' + publication + extension)).resolve()) for extension in ('.png', '.html', '.json', '.txt')}
    files = receipt.get('files')
    require(isinstance(files, list) and len(files) == 4 and {str(Path(file['path']).resolve()) for file in files} == expected, 'RESULT_FILE_SET_INVALID')
    for file in files:
        path = Path(file['path']).resolve()
        require(path.is_file() and path.stat().st_size == file['bytes'] and digest(path.read_bytes()) == file['sha256'], 'OUTPUT_CHANGED')
    image = next(Path(file['path']) for file in files if file['path'].endswith('.png'))
    raw = image.read_bytes()
    require(len(raw) > 1000 and raw.startswith(b'\x89PNG\r\n\x1a\n') and struct.unpack('>II', raw[16:24])[0] == 780, 'PNG_INVALID')


def accept(args):
    require(args.visual_reviewed, 'ACTUAL_VISUAL_REVIEW_REQUIRED')
    project = Path(args.workspace).resolve()
    receipt = load(project / 'RESULT.json')
    validate_result(receipt, project)
    # Revalidation prevents a visual receipt from accepting changed research.
    data = snapshot(project)
    expected = next(Path(item['path']) for item in receipt['files'] if item['path'].endswith('.json'))
    require(load(expected) == data, 'RENDERED_INPUT_CHANGED')
    receipt.update(status='REPORT_ACCEPTED', visual_reviewed=True, accepted_at=now().isoformat(), artifacts_ready=True, publication_allowed=True)
    save(project / 'RESULT.json', receipt)
    return receipt


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('doctor', 'prepare', 'select', 'import-news', 'build', 'accept', '_acquire'))
    parser.add_argument('--task', choices=('market', 'finance', 'news'))
    parser.add_argument('--workspace')
    parser.add_argument('--date')
    parser.add_argument('--news-as-of')
    parser.add_argument('--credentials')
    parser.add_argument('--sources')
    parser.add_argument('--market-ids')
    parser.add_argument('--company-ids')
    parser.add_argument('--cache')
    parser.add_argument('--market-budget', type=int, default=120)
    parser.add_argument('--finance-budget', type=int, default=45)
    parser.add_argument('--news-budget', type=int, default=90)
    parser.add_argument('--visual-reviewed', action='store_true')
    args = parser.parse_args(argv)
    result, code = {}, 0
    try:
        require(sys.version_info >= (3, 10), 'PYTHON_310_REQUIRED')
        require(all(5 <= value <= 180 for value in (args.market_budget, args.finance_budget, args.news_budget)), 'ACQUISITION_BUDGET_INVALID')
        require(args.action in ('doctor', 'prepare') or bool(args.workspace), 'WORKSPACE_REQUIRED')
        result = {'doctor': lambda: doctor(args.credentials, args.workspace, args.date, args.news_as_of), 'prepare': lambda: begin(args), 'select': lambda: select_news(args),
                  'import-news': lambda: import_news(args), 'build': lambda: build(args), 'accept': lambda: accept(args), '_acquire': lambda: worker(args)}[args.action]()
    except Failure as exc:
        result, code = {'status': 'FAILED', 'error_code': exc.code, **exc.details}, 1
    except Exception as exc:
        # No raw exception/stringified request/configuration is exported.
        result, code = {'status': 'FAILED', 'error_code': 'UNEXPECTED_FAILURE', 'error_class': type(exc).__name__}, 1
    if code and args.action == 'build':
        result.update(artifacts_ready=False, publication_allowed=False, files=[], prepared_sources_preserved=True)
        if args.workspace:
            result['resume_command'] = command('build', Path(args.workspace).expanduser().resolve())
        if str(result.get('error_code', '')).startswith('BROWSER_') or result.get('error_code') in ('NO_USABLE_BROWSER', 'PLAYWRIGHT_REQUIRED', 'UNEXPECTED_FAILURE'):
            result.update(recovery_scope='operator', retry_allowed=False, dependency_installation_can_repair=False)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
