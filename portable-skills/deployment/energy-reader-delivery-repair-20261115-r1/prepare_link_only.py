"""Apply the user's link-only scheduled Work notification preference."""
import ast
from pathlib import Path

HERE=Path(__file__).resolve().parent
DIRECTORY=HERE/'backend/app/scheduler'

MESSAGE=r'''def _message(notification: WorkNotification, origin: str) -> str:
    try:
        thread = validate_thread_id(notification.thread_id)
    except ValueError:
        thread = ""
    footer_pattern = r'\[点击查看日报网页\]\((https?://[^\s()<>]+)\)'
    footer_lines = [re.fullmatch(footer_pattern, line.strip()) for line in notification.result_text.splitlines()]
    daily = notification.result_text.lstrip().startswith('油气与金融市场日报') or any(footer_lines)
    if daily:
        readers = []
        for footer in footer_lines:
            reader = _daily_reader_url(footer.group(1), origin, thread) if footer else None
            if reader and reader not in readers:
                readers.append(reader)
        # This task's authenticated HTML link is the entire Work message.
        # No title, timestamp, body, attachment claim or conversation fallback.
        return '[点击查看日报网页](' + readers[0] + ')' if len(readers) == 1 else ''
    title = " ".join(notification.title.split())
    summary = re.sub(r"https?://[^\s<>]+", "[链接见平台]", notification.result_text, flags=re.IGNORECASE)
    parts = [f"【定时任务】{title}已完成"]
    if notification.finished_at:
        try:
            finished = datetime.fromisoformat(notification.finished_at)
            finished = (finished if finished.tzinfo else finished.replace(tzinfo=UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
            parts.append(f"完成时间：{finished:%Y-%m-%d %H:%M}（北京时间）")
        except ValueError:
            pass
    if summary.strip():
        parts.append(summary.strip())
    parts.append(f"查看完整结果：{origin}/workspace/chats/{thread}" if origin and thread else "请在智能体平台的定时任务运行记录中查看完整结果。")
    return "\n\n".join(parts)
'''

SUMMARY=r'''def _notification_summary(value: str | None) -> str:
    text = (value or "").strip()
    footers = []
    for line in text.splitlines():
        candidate = line.strip()
        if (len(candidate.encode('utf-8')) <= 2048
                and re.fullmatch(r'\[点击查看日报网页\]\(https://[^\s()<>]+\)', candidate)
                and candidate not in footers):
            footers.append(candidate)
            if len(footers) > 2:
                # An ambiguous report is never silently reduced to one date.
                return '油气与金融市场日报'
    if text.startswith('油气与金融市场日报') or footers:
        # The sender validates the current task, trusted origin and real date.
        # Store no report body in this scheduled notification's private outbox.
        return '油气与金融市场日报' + ('\n\n' + '\n\n'.join(footers) if footers else '')
    return text[:1200]
'''

DELIVER=r'''    async def deliver(notification: WorkNotification) -> DeliveryResult:
        message = _message(notification, origin)
        if not message:
            return "failed"
        try:
            await send_work_notification(
                notification.owner_user_id,
                notification.connection_id,
                message,
                notification.delivery_id,
                binding_key=notification.binding_key,
                **({"markdown": True} if message.startswith("[点击查看日报网页](") else {}),
            )
        except WorkNotificationError as exc:
            if exc.uncertain:
                return "uncertain"
            return "retry" if exc.stage == "sender_unavailable" else "failed"
        return "sent"
'''

def replace(source,name,replacement,asynchronous=False):
    kind=ast.AsyncFunctionDef if asynchronous else ast.FunctionDef
    nodes=[n for n in ast.walk(ast.parse(source)) if isinstance(n,kind) and n.name==name]
    assert len(nodes)==1
    node=nodes[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[replacement+'\n'];return ''.join(lines)

def main():
    path=DIRECTORY/'work_notification_delivery.py';source=path.read_text(encoding='utf-8')
    source=replace(source,'_message',MESSAGE);source=replace(source,'deliver',DELIVER,True)
    compile(source,str(path),'exec');path.write_bytes(source.encode())
    path=DIRECTORY/'work_notifications.py';source=replace(path.read_text(encoding='utf-8'),'_notification_summary',SUMMARY)
    compile(source,str(path),'exec');path.write_bytes(source.encode())
    print('LINK_ONLY_WORK_NOTIFICATION_CANDIDATE_PREPARED_NOT_DEPLOYED')

if __name__=='__main__':main()
