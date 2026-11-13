"""Versioned successor for explicitly evidenced nullable market observations.

The sealed upstream files are never edited. Complete input uses the original
validator/loader/renderer. Only market absence has a successor policy; finance
and news keep the upstream checks. This module performs no network requests.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import types
from pathlib import Path

POLICY = "energy-market-nullable.v2"
VALIDATOR = "energy-market-nullable-validator.v2"
KINDS = (("oil", 3), ("gas", 3), ("freight", 8))
FAILURES = {"transport_failed", "invalid_response", "not_fetched"}
MODULES = ("fetch_energy_data", "fetch_market_history", "report_data", "report_renderer", "generate_report", "validate_inputs")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    """Reject links/junctions before resolving any caller-controlled path."""
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(), "PATH_LINK_REJECTED")
    return path


def read(path, limit=8 * 1024 * 1024):
    path = safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, "EVIDENCE_SIZE_OR_TYPE")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "EVIDENCE_CHANGED")
        require(len(raw) == before.st_size and len(raw) <= limit, "EVIDENCE_SIZE_BOUND")
    finally:
        os.close(fd)
    return raw


def write_bytes(path, raw):
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.stage-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        safe_path(path)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def text_bytes(text):
    # Match upstream Path.write_text's platform newline behavior byte-for-byte.
    return text.replace('\n', os.linesep).encode('utf-8')


def save(path, data):
    write_bytes(path, text_bytes(json.dumps(data, ensure_ascii=False, indent=2)))


def dates(day):
    publication = dt.date.fromisoformat(day)
    require(publication.isoformat() == day, "PUBLICATION_DATE")
    target = publication - dt.timedelta(days=1)
    return publication, target


@contextlib.contextmanager
def original(engine):
    """Load one sealed engine's functions without leaking ROOT across sessions."""
    app = safe_path(safe_path(engine).resolve() / "app")
    require(app.is_dir() and not app.is_symlink(), "ENGINE_APP_REQUIRED")
    previous = {name: sys.modules.pop(name, None) for name in MODULES}
    sys.path.insert(0, str(app))
    try:
        yield types.SimpleNamespace(**{name: importlib.import_module(name) for name in MODULES})
    finally:
        sys.path.pop(0)
        for name, module in previous.items():
            sys.modules.pop(name, None)
            if module is not None:
                sys.modules[name] = module


def expected_days(o, day):
    _, target = dates(day)
    start = o.generate_report.previous_months(target, 3)
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((target - start).days + 1)]


def load_batch(root, observation, o, day):
    """Reconcile the exact announced batch; never inspect ambient dated cache."""
    root = safe_path(root).resolve()
    days = expected_days(o, day)
    target = days[-1]
    require(isinstance(observation, dict) and observation.get("data_date") == target, "OBSERVATION_DATE")
    if observation.get("status") == "LOCAL_ACQUISITION_FAILURE":
        require(observation.get("failure_code") in {
            "MARKET_WAIT_TIMEOUT", "COLLECTOR_UNREACHABLE", "WAIT_PROVIDER_DATA",
            "COLLECTOR_FAILED", "COLLECTOR_STATE_UNAVAILABLE", "COLLECTOR_INVALID_RESPONSE"}, "LOCAL_FAILURE_CODE")
        require(observation.get("target_response_validated") is False
                and "manifest_file" not in observation and "manifest_sha256" not in observation, "LOCAL_FAILURE_EVIDENCE")
        timestamp = observation.get("observed_at")
        require(isinstance(timestamp, str) and dt.datetime.fromisoformat(timestamp).tzinfo is not None, "OBSERVATION_TIME")
        historical = observation.get('historical_responses', [])
        require(isinstance(historical, list) and len(historical) < len(days), 'LOCAL_HISTORY_ROWS')
        records, files, seen = [], {}, set()
        required = set(o.fetch_market_history.SPECS) | set(o.fetch_market_history.FREIGHT)
        for row in historical:
            date, filename, sha = row.get('date'), row.get('file'), row.get('sha256')
            require(date in days[:-1] and date not in seen, 'LOCAL_HISTORY_DATE')
            require(isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha)
                    and filename == 'market-responses/' + sha + '.json'
                    and row.get('origin') == 'verified_historical_cache', 'LOCAL_HISTORY_REFERENCE')
            raw = read(root / filename, 256 * 1024)
            require(type(row.get('bytes')) is int and len(raw) == row['bytes'] and digest(raw) == sha, 'LOCAL_HISTORY_HASH')
            records.extend(r for r in o.fetch_market_history.normalize(json.loads(raw), date) if r['code'] in required)
            files[filename] = raw
            seen.add(date)
        return dict(days=days, records=records, gaps=[d for d in days if d not in seen], target_state="transport_failed",
                    timestamp=timestamp, files=files, observation=observation, batch_id=None)
    name, sha = observation.get("manifest_file"), observation.get("manifest_sha256")
    require(isinstance(name, str) and re.fullmatch(r"market-state_" + re.escape(target) + r"_[a-f0-9]{32}\.json", name), "MANIFEST_NAME")
    require(isinstance(sha, str) and re.fullmatch(r"[a-f0-9]{64}", sha), "MANIFEST_HASH")
    raw = read(root / name, 256 * 1024)
    require(digest(raw) == sha, "MANIFEST_HASH_MISMATCH")
    manifest = json.loads(raw)
    require(manifest.get("schema") == "energy-market-batch.v2" and manifest.get("batch_id") == name[-37:-5], "MANIFEST_SCHEMA")
    require(manifest.get("data_date") == target and manifest.get("history_start") == days[0]
            and manifest.get("history_end") == target, "MANIFEST_WINDOW")
    timestamp = manifest.get("created_at")
    require(isinstance(timestamp, str) and dt.datetime.fromisoformat(timestamp).tzinfo is not None, "MANIFEST_TIME")
    responses, failures = manifest.get("responses"), manifest.get("failures")
    require(isinstance(responses, list) and isinstance(failures, list)
            and len(responses) <= len(days) and len(failures) <= len(days), "MANIFEST_ROWS")
    records, seen, failed, files = [], set(), set(), {name: raw}
    required = set(o.fetch_market_history.SPECS) | set(o.fetch_market_history.FREIGHT)
    for row in responses:
        date, filename, filehash = row.get("date"), row.get("file"), row.get("sha256")
        require(date in days and date not in seen, "RESPONSE_DATE")
        require(isinstance(filehash, str) and re.fullmatch(r"[a-f0-9]{64}", filehash)
                and filename == "market-responses/" + filehash + ".json", "RESPONSE_PATH")
        require(row.get("origin") in {"provider_this_batch", "verified_historical_cache"}
                and (date != target or row["origin"] == "provider_this_batch"), "TARGET_MUST_BE_FRESH")
        path = root / filename
        require(path.parent.resolve() == root / "market-responses", "RESPONSE_PARENT")
        payload = read(path, 256 * 1024)
        require(type(row.get("bytes")) is int and len(payload) == row["bytes"] and digest(payload) == filehash, "RESPONSE_HASH_MISMATCH")
        normalized = o.fetch_market_history.normalize(json.loads(payload), date)
        # Extra supplier series are not report slots, matching upstream build.
        records.extend(r for r in normalized if r["code"] in required)
        seen.add(date)
        files[filename] = payload
    for row in failures:
        require(row.get("date") in days and row["date"] not in seen and row["date"] not in failed
                and row.get("category") in FAILURES, "FAILURE_ROW")
        failed.add(row["date"])
    gaps = [date for date in days if date not in seen]
    target_rows = [r for r in records if r["date"] == target]
    counts = {state: sum(r["status"] == state for r in target_rows) for state in ("available", "non_trading", "missing")}
    require(manifest.get("required_target_counts") == counts, "TARGET_COUNTS_MISMATCH")
    require(manifest.get("target_response_validated") is bool(target_rows), "TARGET_VALIDATION_MISMATCH")
    if target_rows:
        state = "provider_missing" if counts["missing"] else "non_trading" if counts["non_trading"] else "available"
        require(manifest.get("target_state") == state, "TARGET_STATE_MISMATCH")
    else:
        state = manifest.get("target_state")
        require(state in FAILURES, "TARGET_FAILURE_STATE")
    status = "INCOMPLETE_DATA" if gaps or counts["missing"] else "ANNOTATED_READY" if counts["non_trading"] else "READY"
    require(manifest.get("status") == status and observation.get("status") == status, "BATCH_STATUS_MISMATCH")
    return dict(days=days, records=records, gaps=gaps, target_state=state,
                timestamp=timestamp, files=files, observation=observation, batch_id=manifest["batch_id"])


def build_nullable(o, batch):
    days, records = batch["days"], batch["records"]
    start, target = days[0], days[-1]
    f = o.fetch_market_history
    target_rows = {r["code"]: r for r in records if r["date"] == target}
    missing = sum(r["status"] == "missing" for r in target_rows.values())
    holidays = sum(r["status"] == "non_trading" for r in target_rows.values())
    if not batch["gaps"] and not missing and not holidays:
        return f.build(start, target, records), None
    incomplete = bool(batch["gaps"] or missing)
    quality = dict(policy_version=POLICY, validator_id=VALIDATOR,
                   quality="incomplete" if incomplete else "holiday", formal_report_ready=not incomplete,
                   target_missing_count=missing, target_unavailable_count=14 - len(target_rows),
                   target_non_trading_count=holidays, historical_technical_gap_count=sum(d != target for d in batch["gaps"]))
    result = dict(data_date=target, start=start, end=target, retrieved_at=batch["timestamp"],
                  oil=[], gas=[], freight=[], history=dict(start=start, end=target, single_day=False),
                  observations=records, policy_version=POLICY, report_quality=quality)
    for code in list(f.SPECS) + f.FREIGHT:
        rows = sorted((r for r in records if r["code"] == code), key=lambda r: r["date"])
        current = target_rows.get(code)
        metadata = current or (rows[-1] if rows else None)
        kind, name, unit = f.SPECS[code] if code in f.SPECS else (
            "freight", metadata["item"] if metadata else "航线 " + code,
            metadata["unit"] if metadata else "单位待验证")
        previous = next((r for r in reversed(rows) if r["date"] < target and r["value"] is not None), None)
        value = current["value"] if current else None
        same = value is not None and previous and previous["unit"] == unit
        status = current["status"] if current else "unavailable"
        q = dict(name=name, code=code, unit=unit, date=target, value=value,
                 change=(value / previous["value"] - 1) * 100 if same else None,
                 previous_date=previous["date"] if same else None, previous_value=previous["value"] if same else None,
                 source="金凯讯", contract=current["item"] if current else None,
                 basis="接口close；名称及单位沿用接口" if current else "目标日未取得有效响应；不使用历史报价替代",
                 decimals=3 if kind == "gas" else 2,
                 change_method="calculated" if same else "unit_changed_or_missing_previous", status=status)
        if value is None:
            q["missing_reason"] = {"non_trading": "供应商目标日明确标注休市", "missing": "供应商目标日标注暂无或未更新", "unavailable": "目标日取数失败，未获得可验证报价"}[status]
        result[kind].append(q)
        segments, segment = [], None
        by_day = {r["date"]: r for r in rows}
        pending_gap = False
        for date in days:
            r = by_day.get(date)
            if r is None:
                pending_gap = True
                continue
            if segment is None or segment["unit"] != r["unit"]:
                segment = dict(name=name, code=code, unit=r["unit"], axis="right" if code == "NY0421" else "left", dates=[], values=[], break_before=[])
                segments.append(segment)
            if pending_gap or r["status"] == "missing":
                segment["break_before"].append(len(segment["dates"]))
            pending_gap = False
            if r["value"] is not None:
                segment["dates"].append(r["date"])
                segment["values"].append(r["value"])
        result["history"].setdefault(kind, []).extend(s for s in segments if s["dates"])
    return result, quality


def import_market(engine, day, cache, observation_path):
    engine = safe_path(engine).resolve()
    cache = safe_path(cache).resolve()
    observation_raw = read(observation_path, 32 * 1024)
    observation = json.loads(observation_raw)
    with original(engine) as o:
        if observation.get('status') == 'LOCAL_ACQUISITION_FAILURE':
            # Preserve only strictly normalized past cache supplied by the trusted
            # collector feed. Never inspect the target's old cache or example data.
            require('historical_responses' not in observation, 'LOCAL_HISTORY_MUST_BE_COLLECTED_HERE')
            historical, historical_files = [], {}
            for date in expected_days(o, day)[:-1]:
                path = cache / ('jkx_new_' + date + '.json')
                if not path.exists():
                    continue
                raw = read(path, 256 * 1024)
                payload = json.loads(raw)
                normalized = o.fetch_market_history.normalize(payload, date)
                # Whitelist the exact fields consumed by the original normalizer.
                clean = {'errcode': '0', 'data': [{k: row[k] for k in ('code_name', 'value_date', 'close', 'unit', 'item')} for row in payload['data']]}
                require(o.fetch_market_history.normalize(clean, date) == normalized, 'HISTORICAL_SANITIZATION_CHANGED')
                raw = json.dumps(clean, ensure_ascii=False, indent=2).encode('utf-8')
                sha = digest(raw)
                filename = 'market-responses/' + sha + '.json'
                historical.append(dict(date=date, file=filename, sha256=sha, bytes=len(raw), origin='verified_historical_cache'))
                historical_files[filename] = raw
            observation['historical_responses'] = historical
            observation_raw = json.dumps(observation, ensure_ascii=False, indent=2).encode('utf-8')
            evidence = engine / 'app/sources/market-v2' / digest(observation_raw)
            for filename, raw in historical_files.items():
                write_bytes(evidence / filename, raw)
            # The frozen bundle is also the source used during offline rechecks.
            batch = load_batch(evidence, observation, o, day)
        else:
            batch = load_batch(cache, observation, o, day)
        result, quality = build_nullable(o, batch)
    # Save only validated, bounded supplier evidence in the private session.
    evidence = engine / "app/sources/market-v2" / digest(observation_raw)
    require(not evidence.is_symlink(), "EVIDENCE_DIRECTORY_SYMLINK")
    evidence.mkdir(parents=True, exist_ok=True)
    for name, raw in dict(batch["files"], **{"observation.json": observation_raw}).items():
        destination = evidence / name
        require(not destination.is_symlink(), "EVIDENCE_SYMLINK")
        destination.parent.mkdir(parents=True, exist_ok=True)
        require(destination.parent.resolve().is_relative_to(evidence.resolve()), "EVIDENCE_PARENT")
        write_bytes(destination, raw)
    if quality:
        result["market_evidence"] = dict(observation_file=(evidence / "observation.json").relative_to(engine / "app").as_posix(), observation_sha256=digest(observation_raw))
    target = dates(day)[1].isoformat()
    save(engine / "app/data" / ("energy_" + target + ".json"), result)
    return dict(status="MARKET_INPUT_IMPORTED", report_quality=quality, policy_version=POLICY if quality else None)


def validate_non_market(o, day, energy):
    """The unchanged upstream financial/news checks, with its own helpers.

    Deliberately no fake market quotes and no monkeypatch of upstream.validate.
    The successor owns only the market loop it replaces.
    """
    v = o.validate_inputs
    publication, target = dates(day)
    t = target.isoformat()
    financial = json.loads(read(v.ROOT / "data" / f"financial_{t}.json"))
    news = json.loads(read(v.ROOT / "data" / f"news_{day}.json"))
    v.require(energy['data_date'] == t and financial['data_date'] == t and news['publication_date'] == day, 'Snapshot date mismatch')
    for kind, codes, units in [('stocks', v.STOCK_CODES, ['元', '港元', '元', '元', '点']), ('fx', v.FX_CODES, ['点', '元/美元'])]:
        v.require(len(financial[kind]) == len(codes), 'Incomplete financial set')
        for q, allowed, unit in zip(financial[kind], codes, units):
            v.require(q['code'] in allowed and q['unit'] == unit and q['date'] == t, 'Financial identity/unit/date mismatch')
            v.evidence(q)
            if q['value'] is None:
                v.require(q.get('status') in ['missing', 'non_trading'] and q['change'] is None and q.get('missing_reason'), 'Unexplained financial missing quote')
                continue
            v.require(v.number(q['value']) and q['value'] > 0 and q.get('basis'), 'Invalid financial quote/basis')
            if q['change'] is None:
                v.require(q.get('change_method') == 'missing_previous', 'Unexplained missing change')
            elif q.get('change_method') == 'reported':
                v.require(v.number(q['change']), 'Invalid reported change')
            else:
                v.require(v.number(q.get('previous_value')) and q['previous_value'] > 0 and q.get('previous_date', '9999') < t, 'Invalid financial previous')
                v.require(math.isclose(q['change'], (q['value'] / q['previous_value'] - 1) * 100, abs_tol=1e-9), 'Financial change mismatch')
    cutoff = dt.datetime.fromisoformat(news['news_as_of'])
    v.require(cutoff.tzinfo is not None, 'News cutoff must include timezone')
    v.require(cutoff.date() <= publication, 'News cutoff after publication')
    v.require(3 <= len(news['market_news']) <= 5 and len(news['company_news']) >= 5, 'News counts fail')
    all_news = news['market_news'] + news['company_news']
    v.require(len({q['url'] for q in all_news}) == len(all_news), 'Duplicate article across sections')
    for q in all_news:
        v.require(q.get('title') and q.get('text'), 'Empty article')
        published = dt.date.fromisoformat(q['published'])
        v.require(publication - dt.timedelta(days=7) <= published <= publication and published <= cutoff.date(), 'News outside time window')
        if q.get('published_at'):
            v.require(dt.datetime.fromisoformat(q['published_at']) <= cutoff, 'News published after cutoff')
        v.evidence(q)
        # Also retain report_data.load_report's stricter news URL rule.
        v.require(q['url'].startswith('https://'), 'Invalid news date or source URL')
    return financial, news


def checked_data(engine, day, o):
    app = safe_path(engine).resolve() / "app"
    target = dates(day)[1].isoformat()
    energy_path = app / "data" / ("energy_" + target + ".json")
    energy = json.loads(read(energy_path))
    if "policy_version" not in energy:
        o.validate_inputs.validate(day)
        publication, target_day = dates(day)
        snapshot = o.report_data.load_report(publication, o.generate_report.previous_months(target_day, 3), app / "data", app / "data" / ("news_" + day + ".json"))
        return snapshot, None
    require(energy.get("policy_version") == POLICY, "UNKNOWN_MARKET_POLICY")
    ref = energy.get("market_evidence", {})
    name, sha = ref.get("observation_file"), ref.get("observation_sha256")
    require(isinstance(name, str) and re.fullmatch(r"sources/market-v2/[a-f0-9]{64}/observation\.json", name), "OBSERVATION_REFERENCE")
    path = app / name
    require(path.parent.resolve() == app / "sources/market-v2" / name.split("/")[2], "OBSERVATION_PARENT")
    raw = read(path, 32 * 1024)
    require(digest(raw) == sha == path.parent.name, "OBSERVATION_HASH_MISMATCH")
    batch = load_batch(path.parent, json.loads(raw), o, day)
    expected, quality = build_nullable(o, batch)
    require(quality is not None, "SUCCESSOR_POLICY_NOT_NEEDED")
    expected["market_evidence"] = ref
    require(expected == energy, "MARKET_SNAPSHOT_EVIDENCE_MISMATCH")
    financial, news = validate_non_market(o, day, energy)
    stocks = [dict(x) for x in financial["stocks"]]
    stocks[0]["name"] = "海油 A 股"
    source_paths = [energy_path, app / "data" / ("news_" + day + ".json"), app / "data" / ("financial_" + target + ".json")]
    snapshot = dict(publication_date=day, data_date=target, oil=energy["oil"], gas=energy["gas"], freight=energy["freight"],
                    stocks=stocks, fx=financial["fx"], history=energy["history"],
                    sources=[dict(file=p.name, sha256=digest(read(p))) for p in source_paths],
                    market_news=news["market_news"], company_news=news["company_news"], news_as_of=news["news_as_of"],
                    gas_source="金凯讯", api_energy=True, report_quality=quality, policy_version=POLICY)
    return snapshot, quality


def render_nullable(renderer, snapshot):
    """Reuse the original page/layout with explicitly replaced nullable widgets."""
    def cards(items):
        parts = []
        for item in items:
            # Original renderer handles financial missing/non-trading unchanged.
            card = renderer.cards([item])
            if item.get("status") == "unavailable":
                card = card.replace("未接入", "获取失败")
            elif item.get("status") == "missing" and item.get("source") == "金凯讯":
                card = card.replace("待核实", "暂无数据")
            parts.append(card[card.index('>') + 1:-6])
        return f'<div class="quotes cols-{len(items)}">' + ''.join(parts) + '</div>'

    def chart(history, kind):
        if not any(s.get("values") for s in history.get(kind, [])):
            return '<div class="chart"><p class="small-note">暂无可验证的历史数据；未填补价格。</p></div>'
        rendered = renderer.chart(history, kind)
        if kind == 'gas':
            notes = []
            if not any(s.get('values') for s in history[kind] if s['axis'] == 'left'):
                rendered = re.sub(r'<text x="32" y="[^"]+" text-anchor="end" class="axis">[^<]*</text>', '', rendered)
                notes.append('左轴暂无有效历史数据')
            if not any(s.get('values') for s in history[kind] if s['axis'] == 'right'):
                rendered = re.sub(r'<text x="325" y="[^"]+" class="axis">[^<]*</text>', '', rendered)
                rendered = rendered.replace('Henry Hub（右轴）</text>', 'Henry Hub（右轴暂无数据）</text>')
                notes.append('右轴暂无有效历史数据')
            if notes:
                rendered += '<p class="small-note">' + '；'.join(notes) + '，该轴不显示数值刻度。</p>'
        return rendered

    def freight_panel(data):
        rows = []
        for i in range(0, len(data['freight']), 2):
            a, b = data['freight'][i:i + 2]
            cells = []
            for q in (a, b):
                change = q['change']
                state = 'flat' if change is None or change == 0 else 'up' if change > 0 else 'down'
                delta = '口径变化，不比涨跌' if change is None else f'{"▲" if change > 0 else "▼" if change < 0 else "—"} {abs(change):.2f}%'
                if q['value'] is None:
                    delta = {'non_trading': '休市', 'missing': '暂无数据', 'unavailable': '获取失败'}[q['status']]
                value = '—' if q['value'] is None else f'{q["value"]:,.2f}'
                cells.append(f'<td><b>{value}</b><span>{renderer.esc(q["unit"])}</span><small class="{state}">{delta}</small></td>')
            rows.append(f'<tr><th>{renderer.esc(a["name"].split("（")[0])}</th>{"".join(cells)}</tr>')
        body = '<table class="freight-table"><thead><tr><th>航线</th><th>整船口径</th><th>每桶口径</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table>'
        series = [s for s in data['history']['freight'] if s['unit'] == '万美元']
        body += chart(dict(data['history'], freight=series), 'freight')
        return body + '<p class="small-note">数据来源：金凯讯；走势图为整船口径（万美元）。</p>'

    # A new function object with named successor widget bindings; upstream module
    # globals, function objects and all original source files remain unchanged.
    render = types.FunctionType(renderer.render.__code__, dict(renderer.render.__globals__, cards=cards, chart=chart, freight_panel=freight_panel))
    html = render(snapshot)
    if not snapshot['history'].get('freight'):
        # The original fallback predates known eight-slot nullable freight. Keep
        # the actual table and an explicit empty-history note, without fake points.
        old = '<section><div class="section-head"><h2>航运运费</h2><span>指标及数据源待确认</span></div>' + cards(snapshot['freight']) + '</section>'
        new = '<section><div class="section-head"><h2>航运运费</h2><span>较前一有效报价</span></div>' + freight_panel(snapshot) + '</section>'
        require(html.count(old) == 1, 'NULLABLE_FREIGHT_LAYOUT_CHANGED')
        html = html.replace(old, new, 1)
    quality = snapshot['report_quality']
    if quality['formal_report_ready']:
        note = '目标日部分或全部市场由供应商明确标注休市；报价留空，历史曲线只显示有效报价。'
        style = 'background:#eef4fb;border:1px solid #acbed5;color:#294768;'
    else:
        html = html.replace('<h1>油气与金融市场日报</h1>', '<h1>油气与金融市场日报<br>数据不完整报告</h1>')
        html = html.replace('<title>油气与金融市场日报', '<title>数据不完整报告（非正式日报） · 油气与金融市场日报', 1)
        note = ('数据不完整，非正式日报。目标日获取失败 ' + str(quality['target_unavailable_count']) + ' 项，供应商未更新 '
                + str(quality['target_missing_count']) + ' 项；历史取数缺口 ' + str(quality['historical_technical_gap_count'])
                + ' 天。仅展示已核实信息；缺数不补零，不用历史价格代替当日报价。')
        style = 'background:#fff0ed;border:2px solid #b13b2c;color:#8e2418;'
    banner = '<aside role="status" style="' + style + 'padding:10px;margin:12px 0;font-size:12px;line-height:1.7;font-weight:600">' + note + '</aside>'
    return html.replace('</header>', '</header>' + banner, 1)


def validate(engine, day):
    with original(engine) as o:
        _, quality = checked_data(engine, day, o)
    return dict(status="SUCCESSOR_INPUTS_VALIDATED" if quality else "ORIGINAL_INPUTS_VALIDATED", report_quality=quality)


def generate(engine, day, png=False):
    engine = safe_path(engine).resolve()
    with original(engine) as o:
        snapshot, quality = checked_data(engine, day, o)
        html = render_nullable(o.report_renderer, snapshot) if quality else o.report_renderer.render(snapshot)
        path = engine / "app/output" / ("油气与金融市场日报_" + day + ".html")
        path.parent.mkdir(parents=True, exist_ok=True)
        require(not path.is_symlink() and not path.with_suffix('.json').is_symlink() and not path.with_suffix('.png').is_symlink(), "OUTPUT_SYMLINK")
        write_bytes(path, text_bytes(html))
        save(path.with_suffix('.json'), snapshot)
        if png:
            o.generate_report.export_png(path)
    return dict(status="SUCCESSOR_REPORT_GENERATED" if quality else "ORIGINAL_REPORT_GENERATED", report_quality=quality,
                outputs=[str(path), str(path.with_suffix('.json'))] + ([str(path.with_suffix('.png'))] if png else []))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--action', choices=('import-market', 'validate', 'generate'), required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--observation', type=Path)
    parser.add_argument('--png', action='store_true')
    args = parser.parse_args()
    if args.action == 'import-market':
        require(args.cache is not None and args.observation is not None, 'IMPORT_ARGUMENTS_REQUIRED')
        result = import_market(args.engine, args.date, args.cache, args.observation)
    elif args.action == 'validate':
        result = validate(args.engine, args.date)
    else:
        result = generate(args.engine, args.date, args.png)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == '__main__':
    main()
