"""Create a separate 2.1.5 candidate; preserve every accepted 2.1.4 file."""
from pathlib import Path
import shutil

REPO = Path(__file__).resolve().parents[3]
OLD = REPO/'portable-skills/candidates/energy-market-daily-2.1.4'
NEW = REPO/'portable-skills/candidates/energy-market-daily-2.1.5'

def main():
    assert not NEW.exists(), 'CANDIDATE_ALREADY_EXISTS'
    shutil.copytree(OLD,NEW)
    path=NEW/'scripts/daily.py'
    source=path.read_text(encoding='utf-8').replace("VERSION = '2.1.4-portable'", "VERSION = '2.1.5-portable'")
    source=source.replace("frozenset((VERSION, '2.1.3-portable'", "frozenset((VERSION, '2.1.4-portable', '2.1.3-portable'")
    source=source.replace('from delivery_text import host_conversation_context, report_text, require_same_conversation',
                          'from delivery_text import host_conversation_context, report_text, require_same_conversation, html_report_url')
    source=source.replace('    context = host_conversation_context()\n    if (project',
                          "    context = host_conversation_context()\n    context.update(html_report_url=html_report_url(context, data['publication_date']))\n    context['html_report_link_available'] = bool(context['html_report_url'])\n    if (project",1)
    source=source.replace("(VERSION, '2.1.3-portable', '2.1.2-portable')", "(VERSION, '2.1.4-portable', '2.1.3-portable', '2.1.2-portable')")
    source=source.replace("(VERSION, '2.1.3-portable')", "(VERSION, '2.1.4-portable', '2.1.3-portable')")
    source=source.replace("if receipt['version'] == VERSION:\n            require(receipt['png']", "if receipt['version'] in (VERSION, '2.1.4-portable'):\n            require(receipt['png']")
    source=source.replace('report_text(data, reply_text(data), context), \'RESULT_REPLY_TEXT_MISMATCH\'',
                          "report_text(data, reply_text(data), context, include_html_link=receipt['version'] == VERSION), 'RESULT_REPLY_TEXT_MISMATCH'")
    path.write_text(source,encoding='utf-8')
    path=NEW/'scripts/delivery_text.py'
    source=path.read_text(encoding='utf-8').replace('import ipaddress','import datetime as dt\nimport ipaddress')
    source=source.replace('from urllib.parse import urlsplit','from urllib.parse import urlsplit, urlunsplit')
    start=source.index('def report_text(')
    end=source.index('\n\ndef conversation_summary',start)
    source=source[:start]+'''def html_report_url(context, publication_date):
    """Use the deployed reader contract and the current host-owned chat identity."""
    url = public_conversation_url(context.get('conversation_url'))
    if not url:
        return None
    require(isinstance(publication_date, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', publication_date),
            'DELIVERY_PUBLICATION_DATE_INVALID')
    try:
        require(dt.date.fromisoformat(publication_date).isoformat() == publication_date, 'DELIVERY_PUBLICATION_DATE_INVALID')
    except ValueError:
        raise Failure('DELIVERY_PUBLICATION_DATE_INVALID') from None
    parsed = urlsplit(url)
    thread = parsed.path.rsplit('/', 1)[1]
    return urlunsplit((parsed.scheme, parsed.netloc, '/reports/energy-daily/' + thread + '/' + publication_date + '.html', '', ''))


def report_text(data, reply_text, context, include_html_link=True):
    """Prepare both links in the same TXT; delivery still requires present_files."""
    require(isinstance(reply_text, str) and reply_text.startswith('油气与金融市场日报'), 'DELIVERY_TEXT_INVALID')
    require(len(reply_text.encode()) <= MAX_TEXT_BYTES, 'DELIVERY_TEXT_SIZE_LIMIT')
    require('发布日期：' + data['publication_date'] + '；行情日期：' + data['data_date'] in reply_text
            and '报告状态：' + data['quality'] in reply_text, 'DELIVERY_TEXT_REPORT_MISMATCH')
    url = context.get('conversation_url')
    if not url:
        return reply_text
    require(public_conversation_url(url) is not None, 'HOST_CONVERSATION_URL_INVALID')
    footer = '[点击查看完整日报](' + url + ')'
    reader = html_report_url(context, data['publication_date']) if include_html_link else None
    links = ('[点击查看日报网页](' + reader + ')\\n\\n' if reader else '') + footer
    body = reply_text.rstrip()
    if body.endswith(links):
        return body + '\\n'
    # Compatibility finalization may start with the previously linked TXT.
    if body.endswith(footer):
        body = body[:-len(footer)].rstrip()
    require(not re.search(r'^\\[点击查看(?:完整日报|日报网页)\\]\\(', body, re.M), 'DELIVERY_TEXT_LINK_CONFLICT')
    return body + '\\n\\n' + links + '\\n'
'''+source[end:]
    source=source.replace("('2.1.3-portable', '2.1.4-portable')", "('2.1.3-portable', '2.1.4-portable', '2.1.5-portable')")
    path.write_text(source,encoding='utf-8')
    path=NEW/'SKILL.md'
    source=path.read_text(encoding='utf-8').replace('2.1.4','2.1.5')
    source=source.replace('逐项运费日期和当前对话链接', '逐项运费日期、“点击查看日报网页”的 HTML 阅读链接和当前对话链接')
    source += '\nHTML 阅读链接由程序用平台注入的当前任务身份及本期发布日期生成，和完整正文一起写入本次 TXT；不要自行构造 URL、再请求链接、重新总结或另发补充。阅读链接只在四文件真实发布成功后随最终正文交付。网页对话与获授权的 Work 回复使用完全相同的链接。打开时需登录海能，并沿用该对话文件的读取权限；这不是公开分享链接。HTML 仍保留为四件交付文件之一。\n'
    path.write_text(source,encoding='utf-8')
    (NEW/'CANDIDATE.md').write_text('2.1.5 candidate: authenticated HTML reader link in the existing single final TXT. Acquisition, calculation, layout and browser runtime unchanged. Not deployed.\n',encoding='utf-8')
    for path in (NEW/'scripts').glob('*.py'):
        compile(path.read_bytes(),str(path),'exec')
    print(str(NEW))

if __name__=='__main__':
    main()
