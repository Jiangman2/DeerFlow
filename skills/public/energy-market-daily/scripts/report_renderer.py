"""Compact, self-contained HTML with source-linked news and date-aware charts."""
import datetime as dt
import html
import math


def esc(value):
    return html.escape(str(value), quote=True)


def cards(items):
    result = []
    for item in items:
        change = item['change']
        state, arrow = ('flat', '—') if change is None or change == 0 else ('up', '▲') if change > 0 else ('down', '▼')
        value = '—' if item['value'] is None else f"{item['value']:,.{item.get('decimals', 2)}f}"
        delta = ('前值待补' if item['value'] is not None else '未接入') if change is None else f'{arrow} {abs(change):.2f}%'
        if item.get('status') == 'non_trading':
            delta = '休市'
        elif item.get('status') == 'missing':
            delta = '待核实'
        if item.get('url'):
            value = f'<a class="price-link" href="{esc(item["url"])}" title="{esc(item["source"])} · {esc(item["date"])}" target="_blank" rel="noopener noreferrer">{value}</a>'
        label = esc(item['name'])
        label = label.replace('布伦特原油', '布伦特原油<br>').replace('WTI 原油期货', 'WTI 原油<br>期货').replace('上海原油期货（SC）', '上海原油<br>期货（SC）').replace('东北亚 LNG 现货（East Asia spot）', '东北亚 LNG 现货<br><span class="name-en">（East Asia spot）</span>')
        result.append(f'<article class="quote"><div class="quote-name">{label}</div>'
                      f'<strong>{value}</strong><span class="unit">{esc(item["unit"])}</span>'
                      f'<span class="change {state}">{delta}</span></article>')
    return f'<div class="quotes cols-{len(items)}">'+''.join(result)+'</div>'


COLORS = ['#35558a', '#27a598', '#e96a55', '#8b7cb2']


def chart(history, kind):
    series = history[kind]
    if not series:
        return '<div class="chart"><p class="small-note">本期未取得可核实的历史曲线，不补零或沿用旧价。</p></div>'
    is_oil = kind == 'oil'
    is_gas = kind == 'gas'
    left, right, top, bottom = 38, 320 if is_gas else 342, 20, 112
    start, end = dt.date.fromisoformat(history['start']), dt.date.fromisoformat(history['end'])
    def limits(values, precise=False):
        if not values:
            return 0, 1
        lo, hi = min(values), max(values)
        if precise:
            pad = max((hi-lo)*0.12, 0.05)
            return max(0, math.floor((lo-pad)*10)/10), math.ceil((hi+pad)*10)/10
        pad = max((hi-lo)*0.12, 0.5)
        return max(0, math.floor(lo-pad)), math.ceil(hi+pad)
    lo, hi = limits([v for s in series if s['axis'] == 'left' for v in s['values']])
    rlo, rhi = limits([v for s in series if s['axis'] == 'right' for v in s['values']], precise=not is_oil)
    parts = ['<svg viewBox="0 0 366 135" role="img" aria-label="近三个月价格走势">']
    unit = '美元/桶' if is_oil else '美元/百万英热单位' if is_gas else series[0]['unit']
    parts.append(f'<text x="{left}" y="10" class="axis">'+esc(unit)+'</text>')
    if is_gas:
        parts.append(f'<text x="{right}" y="10" text-anchor="end" class="axis">Henry Hub（右轴）</text>')
    for i in range(4):
        y = top+(bottom-top)*i/3
        parts.append(f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" class="gridline"/>')
        tick=hi-(hi-lo)*i/3
        tick_label=f'{tick:.1f}' if hi-lo<3 else f'{tick:.0f}'
        parts.append(f'<text x="{left-6}" y="{y+3}" text-anchor="end" class="axis">{tick_label}</text>')
        right_label = f'{rhi-(rhi-rlo)*i/3:.0f}' if is_oil else f'{rhi-(rhi-rlo)*i/3:.2f}'
        if is_gas:
            parts.append(f'<text x="{right+5}" y="{y+3}" class="axis">{right_label}</text>')
    for j, item in enumerate(series):
        low, high = (rlo, rhi) if item['axis'] == 'right' else (lo, hi)
        points = [(left+(right-left)*(dt.date.fromisoformat(d)-start).days/(end-start).days,
                   bottom-(v-low)/(high-low)*(bottom-top)) for d, v in zip(item['dates'], item['values'])]
        if not points:
            continue
        breaks = sorted({0, len(points), *item.get('break_before',[])})
        for a,b in zip(breaks,breaks[1:]):
            poly = ' '.join(f'{x:.2f},{y:.2f}' for x, y in points[a:b])
            parts.append(f'<polyline points="{poly}" fill="none" stroke="{COLORS[j%len(COLORS)]}" stroke-width="1.6" stroke-linejoin="round"/>')
        x, y = points[-1]
        parts.append(f'<circle cx="{x}" cy="{y}" r="2" fill="{COLORS[j%len(COLORS)]}"/>')
    for i in range(4):
        d = start+dt.timedelta(days=round((end-start).days*i/3))
        parts.append(f'<text x="{left+(right-left)*i/3}" y="129" text-anchor="middle" class="axis">{d:%m/%d}</text>')
    parts.append('</svg>')
    if history.get('single_day'):
        parts.insert(-1, '<text x="183" y="66" text-anchor="middle" class="axis">仅有当日报价 · 历史序列待接入</text>')
    elif kind == 'freight' and all(len(s['dates']) == 1 for s in series):
        recorded = dt.date.fromisoformat(series[0]['dates'][0]).strftime('%m/%d')
        parts.insert(-1, f'<text x="183" y="66" text-anchor="middle" class="axis">此单位仅{recorded}有记录</text>')
    def legend_name(item):
        if item['axis'] == 'right':
            if item['name'] == '上海原油期货（SC）':
                return '上海原油期货（SC，右轴）'
            return item['name'] + '（右轴）'
        return item['name'].split('（')[0] if kind == 'freight' else item['name']
    legend = ''.join(f'<span><i style="background:{COLORS[j%len(COLORS)]}"></i>{esc(legend_name(item))}</span>' for j, item in enumerate(series))
    return '<div class="chart">'+''.join(parts)+'<div class="legend">'+legend+'</div></div>'


def freight_panel(data):
    rows=[]
    for i in range(0,len(data['freight']),2):
        a,b=data['freight'][i:i+2]
        def cell(q):
            change=q['change']
            state='flat' if change is None or change==0 else 'up' if change>0 else 'down'
            delta='口径变化，不比涨跌' if change is None else f'{"▲" if change>0 else "▼" if change<0 else "—"} {abs(change):.2f}%'
            value = '—' if q['value'] is None else f'{q["value"]:,.2f}'
            if q.get('status') == 'non_trading':
                delta = '休市'
            elif q['value'] is None:
                delta = '数据未取得'
            return f'<td><b>{value}</b><span>{esc(q["unit"])}</span><small class="{state}">{delta}</small></td>'
        rows.append(f'<tr><th>{esc(a["name"].split("（")[0])}</th>{cell(a)}{cell(b)}</tr>')
    body='<table class="freight-table"><thead><tr><th>航线</th><th>整船口径</th><th>每桶口径</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table>'
    for unit in ['万美元']:
        series=[s for s in data['history']['freight'] if s['unit']==unit]
        if series:
            body+=chart(dict(data['history'],freight=series),'freight')
    body+='<p class="small-note">数据来源：金凯讯；走势图为整船口径（万美元）。</p>'
    return body


def news(items):
    return '<div class="news">'+''.join(
        f'<article><p><b>{i}. {esc(x["title"])}。</b>{esc(x["text"])} '
        f'<a href="{esc(x["url"])}" target="_blank" rel="noopener noreferrer">'
        f'{esc(x["source"])} {esc(x["published"][5:].replace("-", "/"))} ↗</a></p></article>'
        for i, x in enumerate(items, 1))+'</div>'


def render(data):
    def section(title, note, body):
        return f'<section><div class="section-head"><h2>{title}</h2><span>{note}</span></div>{body}</section>'
    def dates(items, key='date'):
        return ' / '.join(d[5:].replace('-', '/') for d in sorted(set(x.get(key, '') for x in items)))
    pub = data['publication_date'].replace('-', '.')
    day = data['data_date'].replace('-', '.')
    body = section('原油市场', '较前一交易日', cards(data['oil'])+chart(data['history'], 'oil'))
    body += '<p class="small-note">数据来源：金凯讯</p>'
    body += section('天然气市场', '较前一交易日', '<div class="gas-quotes">'+cards(data['gas'])+'</div>'+chart(data['history'], 'gas'))
    body += '<p class="small-note">数据来源：'+esc(data.get('gas_source','Rystad Energy'))+'</p>'
    body += section('航运运费', '较前一有效报价', freight_panel(data))
    body += section('石油公司与股市', '收盘价｜较前一交易日',
                    cards(data['stocks'][:2])+'<div class="stock-rest">'+cards(data['stocks'][2:])+'</div>')
    fx_note = '美元指数收盘｜在岸人民币16:30' if any('16:30' in x.get('basis','') for x in data['fx']) else '日线收盘 · 在岸人民币'
    body += section('汇率与美元', fx_note, cards(data['fx']))
    body += section('油气市场重要新闻', '', news(data['market_news']))
    extra_slots = ''.join(f'<div class="news-slot"><span>{i}. 公司动态预留</span><span>待补充</span></div>'
                          for i in range(len(data['company_news'])+1, 6))
    body += section('石油公司动态', '', news(data['company_news'])+(extra_slots if not data.get('api_energy') else ''))
    if not data.get('formal_report_ready', True):
        body = '<aside style="background:#fff0df;border:2px solid #b54708;padding:10px;font-size:13px;line-height:1.7;color:#8a3305"><b>数据不完整 · 本期含取数缺口</b><br>缺失项显示“—”，未以历史价格冒充当日；完整性与原因见随附 JSON。</aside>' + body
    history_note = '走势图不对非交易日数据进行补值。'
    if data.get('api_energy') and data['history'].get('single_day'):
        history_note += '油气接口仅提供当日报价，前值与历史序列待补；旧表未拼接。'
    elif data['history'].get('freight'):
        history_note += '未更新数据留空；运费名称、单位按接口口径。'
    financial_sources = '、'.join(dict.fromkeys(x.get('source','') for x in data['stocks']+data['fx'] if x.get('source'))) + '。'
    css = '''
    :root{--navy:#1c3556;--red:#cd4e43;--green:#218478;--ink:#27384c;--muted:#7b8795}
    *{box-sizing:border-box}body{margin:0;background:#eef1f4;font-family:"Microsoft YaHei","PingFang SC",sans-serif;color:var(--ink);-webkit-font-smoothing:antialiased}
    .report{width:100%;max-width:460px;margin:auto;background:#fff;border-top:4px solid var(--navy);padding:17px 14px 12px}
    h1{margin:0;text-align:center;font-size:23px;line-height:1.5;font-weight:700;color:var(--navy);letter-spacing:.3px}
    .byline{display:flex;justify-content:center;gap:12px;margin-top:7px;font-size:10.5px;color:#687788;white-space:nowrap}
    .publisher{text-align:center;font-size:11px;line-height:1.6;margin-top:3px;color:#52677e;letter-spacing:1px}
    section{margin-top:15px}.section-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
    h2{font-size:15px;line-height:20px;margin:0;border-left:3px solid var(--red);padding-left:7px;color:var(--navy);font-weight:600}
    .section-head>span{font-size:10px;color:var(--muted);text-align:right;line-height:1.5;white-space:nowrap}
    .quotes{display:grid;background:#f4f6f9;border:1px solid #edf0f4;border-radius:2px}.cols-4{grid-template-columns:repeat(4,minmax(0,1fr))}.cols-3{grid-template-columns:repeat(3,minmax(0,1fr))}.cols-2{grid-template-columns:repeat(2,minmax(0,1fr))}
    .quote{padding:9px 2px 8px;min-width:0;text-align:center;border-right:1px solid #e3e8ef}.quote:last-child{border-right:0}
    .quote-name{font-size:11px;font-weight:500;line-height:1.5;white-space:normal}.name-en{font-size:10px;white-space:nowrap}
    section:first-of-type .quote-name,.gas-quotes .quote-name{min-height:33px;display:flex;flex-direction:column;justify-content:center}
    .quote strong{display:block;margin-top:5px;font-family:Arial,"Microsoft YaHei",sans-serif;font-size:18px;line-height:1.2;font-weight:600;letter-spacing:-.3px;font-variant-numeric:tabular-nums;white-space:nowrap}
    .unit{display:block;color:var(--muted);font-size:10px;margin-top:3px;white-space:nowrap}.change{display:inline-block;font-size:11px;font-weight:500;margin-top:4px;white-space:nowrap;font-variant-numeric:tabular-nums}
    .gas-quotes .unit{font-size:9px}.price-link{color:inherit;text-decoration:none}
    .stock-rest{margin-top:7px}.news-slot{display:flex;justify-content:space-between;align-items:center;margin-top:8px;padding:13px 10px;border:1px dashed #dce3eb;border-radius:2px;background:#fafbfd;color:#8b95a0;font-size:11px}
    .freight-table{width:100%;border-collapse:collapse;background:#f4f6f9;table-layout:fixed;font-size:11px;text-align:center}.freight-table th{font-weight:500}.freight-table td,.freight-table th{padding:7px 3px;border:1px solid #e3e8ef}.freight-table thead{color:#617084}.freight-table td b{font:600 16px Arial,sans-serif}.freight-table td span{display:block;font-size:9px;color:#7b8795;margin-top:2px}.freight-table small{display:block;font-size:10px;margin-top:3px}
    .up{color:var(--red)}.down{color:var(--green)}.flat{color:#8b95a0;font-weight:400}
    .chart{padding-top:8px}.chart svg{width:100%;height:auto;display:block}
    .axis{font-size:10px;fill:#84909d}.gridline{stroke:#e8edf2;stroke-width:1;stroke-dasharray:2 3}
    .legend{display:flex;justify-content:center;gap:5px 8px;flex-wrap:wrap;color:#617084;font-size:9px;margin-top:4px}
    .legend span{display:flex;gap:3px;align-items:center}.legend i{display:inline-block;width:10px;height:2px}
    .small-note{font-size:10px;line-height:1.5;color:var(--muted);margin:5px 0 0}
    .news article{padding:7px 0;border-bottom:1px solid #edf0f4}.news article:first-child{padding-top:0}.news article:last-child{border-bottom:0;padding-bottom:0}
    .news p{font-size:12.5px;line-height:1.75;color:#536276;margin:0}.news b{font-weight:600;color:var(--ink)}.news a{font-size:10px;color:#788a9e;text-decoration:none;white-space:nowrap}
    footer{margin-top:16px;border-top:1px solid #dce3eb;padding-top:9px;font-size:10px;color:#7b8795;line-height:1.7}
    footer p{margin:0 0 3px;display:flex;gap:6px}footer p:last-child{margin-bottom:0}footer .foot-label{color:#52677e;flex:0 0 30px;font-weight:500}footer .foot-text{flex:1;min-width:0}
    @media(min-width:600px){.report{margin:20px auto;box-shadow:0 6px 35px #1c355610}}@media(max-width:360px){.report{padding-left:10px;padding-right:10px}h1{font-size:22px}.byline{font-size:10px;gap:8px}.quote strong{font-size:16px}.quote-name{font-size:10px}.legend{gap:5px;font-size:9px}}
    '''
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>油气与金融市场日报 · {pub}</title><style>{css}</style></head><body><main class="report"><header><h1>油气与金融市场日报</h1><div class="byline"><span>发布日期：{pub}</span><span>数据日期：{day}</span></div><div class="publisher">能源经济研究院</div></header>{body}<footer><p><span class="foot-label">注：</span><span class="foot-text">{history_note}</span></p><p><span class="foot-label">行情：</span><span class="foot-text">{esc(financial_sources)}</span></p><p><span class="foot-label">新闻：</span><span class="foot-text">来源见段尾标注。</span></p></footer></main></body></html>'''
