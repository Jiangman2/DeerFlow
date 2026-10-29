"""Conservative Work task routing; ordinary chat never grants Work authority."""

import re


def route_work(text, *, links, explicit, files, document_context=False, calendar_context=False, pending_dialogue=False):
    """Ambiguous follow-ups use /work; pending questions cannot capture all chat."""
    text = text.strip()
    if files:
        return "unsupported_attachment" if explicit or links else None
    if re.search(r"换个话题|另一个问题|先不(?:查|处理|安排)|不(?:需要|要|用)(?:再)?(?:访问|调用|使用)?\s*(?:Work|work|海能|日历|日程)", text):
        return None
    if links:
        return "document"
    operation = r"查询|查看|查一下|查下|看看|查|列出|有哪些|有什么|有多少|新增|新建|创建|添加|安排|修改|更新|改|挪|延|提前|推后|取消|删除"
    calendar = r"日历|日程|会议|开会|忙闲|空闲"
    when = r"今天|明天|后天|本周|下周|周[一二三四五六日天]|\d{1,2}月\d{1,2}日"
    if (explicit and re.search(calendar, text)) or (
        not re.search(r"写|润色|翻译|总结|解释|介绍|科普|起源|历史|文案|会议纪要", text) and re.search(operation, text) and (re.search(calendar, text) or re.search(when, text) and re.search(r"安排|空闲|忙", text))
    ):
        return "calendar"
    if calendar_context and re.fullmatch(r"(?:请|把)?(?:刚才|那条|那个|第.{1,3}[条个项]).{0,40}(?:改|挪|延|提前|推后|取消|删除).{0,40}[。！!]?", text):
        return "calendar"
    if pending_dialogue and re.fullmatch(
        r"(?:就|改到|改成|从)?(?:今天|明天|后天|周[一二三四五六日天]|\d{1,2}月\d{1,2}日)?"
        r"(?:上午|下午|晚上|中午)?(?:半个?小时|[零一二三四五六七八九十两\d]+(?:个?小时|分钟|点(?:半|[\d一二三四五六七八九十]+分)?|:\d{2}))"
        r"(?:就够了|就行|即可|左右)?[。！!]?",
        text,
    ):
        return "calendar"
    if explicit:
        return "document"
    if document_context and re.search(r"(?:刚才|这篇|该|那个)(?:的)?(?:文档|文件)|(?:改名|改成|追加|替换)(?:为|成|：|:)", text):
        return "document"
    if re.search(r"\bwork\b|海能", text, re.I) and re.search(r"文档", text) and re.search(operation + r"|读取|总结", text):
        return "document"
    return None
