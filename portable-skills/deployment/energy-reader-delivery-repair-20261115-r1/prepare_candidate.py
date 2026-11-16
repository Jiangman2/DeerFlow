"""Prepare two narrowly changed Work notification source files; never deploy."""
import ast
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
PARENT=REPO/'portable-skills/deployment/gateway-link-r3/backend/app/scheduler'
DEST=HERE/'backend/app/scheduler'

def replace_function(source,name,replacement):
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name==name)
    lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[replacement+'\n']
    return ''.join(lines)


READER=r'''def _daily_reader_url(value: str, origin: str, thread: str) -> str | None:
    """Accept only an unsigned reader URL for this exact task and trusted origin."""
    try:
        target, trusted = urlsplit(value), urlsplit(origin)
        if not thread or target.scheme != 'https' or trusted.scheme != 'https':
            return None
        approved_aliases = {'cnoocclaw.cnooc.com.cn', 'heai.cnooc.com.cn'}
        same_platform = target.hostname == trusted.hostname or (
            target.hostname in approved_aliases and trusted.hostname in approved_aliases
        )
        if (not trusted.hostname or not same_platform
                or target.port not in (None, 443) or trusted.port not in (None, 443)
                or target.username is not None or target.password is not None
                or target.query or target.fragment):
            return None
        match = re.fullmatch(r'/reports/energy-daily/' + re.escape(thread) + r'/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html', target.path)
        if not match or date.fromisoformat(match.group(1)).isoformat() != match.group(1):
            return None
        return origin.rstrip('/') + target.path
    except (TypeError, ValueError):
        return None
'''

SUMMARY=r'''def _notification_summary(value: str | None) -> str:
    text = (value or "").strip()
    if not text.startswith("油气与金融市场日报"):
        return text[:1200]
    if len(text.encode("utf-8")) <= 9000:
        return text
    # Reserve complete Skill-authored footer lines before clipping the body.
    # These remain untrusted input: _message validates the reader's exact
    # origin/thread/date and replaces the conversation footer authoritatively.
    footer = []
    body = []
    for line in text.splitlines():
        candidate = line.strip()
        if (len(candidate.encode('utf-8')) <= 2048
                and re.fullmatch(r'\[点击查看(?:日报网页|完整日报)\]\(https://[^\s()<>]+\)', candidate)):
            if candidate not in footer and len(footer) < 2:
                footer.append(candidate)
        else:
            body.append(line)
    suffix = '\n\n正文较长，完整内容请点击日报链接查看。'
    if footer:
        suffix += '\n\n' + '\n\n'.join(footer)
    budget = 9000 - len(suffix.encode('utf-8'))
    clipped = '\n'.join(body).encode('utf-8')[:budget].decode('utf-8', 'ignore')
    clipped = clipped.rsplit('\n', 1)[0] if '\n' in clipped else '油气与金融市场日报'
    return clipped.rstrip() + suffix
'''


def main():
    DEST.mkdir(exist_ok=True,parents=True)
    for filename in ['work_notification_delivery.py','work_notifications.py']:
        target=DEST/filename
        assert not target.exists(),'CANDIDATE_ALREADY_EXISTS'
        source=(PARENT/filename).read_text(encoding='utf-8')
        if filename=='work_notifications.py':
            source=source.replace('import logging\n','import logging\nimport re\n')
            source=replace_function(source,'_notification_summary',SUMMARY)
        else:
            source=source.replace('from datetime import UTC, datetime','from datetime import UTC, date, datetime')
            source=source.replace('\ndef _message(', '\n'+READER+'\ndef _message(',1)
            marker='    def source_url(match: re.Match) -> str:\n'
            assert source.count(marker)==1
            source=source.replace(marker,'''    try:
        thread = validate_thread_id(notification.thread_id)
    except ValueError:
        thread = ""
    reader_urls = []
    body_lines = []
    for line in notification.result_text.splitlines():
        footer = re.fullmatch(r'\\[点击查看日报网页\\]\\((https?://[^\\s()<>]+)\\)', line.strip()) if daily else None
        reader = _daily_reader_url(footer.group(1), origin, thread) if footer else None
        if reader:
            if reader not in reader_urls:
                reader_urls.append(reader)
            continue
        if daily and line.strip() == '[点击查看完整日报](' + origin.rstrip('/') + '/workspace/chats/' + thread + ')':
            continue
        body_lines.append(line)
    body = '\\n'.join(body_lines) if daily else notification.result_text

'''+marker)
            source=source.replace('source_url, notification.result_text, flags=re.IGNORECASE)', 'source_url, body, flags=re.IGNORECASE)')
            marker='    label = "点击查看完整日报" if daily else "查看完整结果"'
            assert source.count(marker)==1
            source=source.replace(marker,"    if len(reader_urls) == 1:\n        parts.append('[点击查看日报网页](' + reader_urls[0] + ')')\n"+marker)
        compile(source,str(target),'exec')
        target.write_bytes(source.encode('utf-8'))
    print(str(DEST))


if __name__=='__main__':main()
