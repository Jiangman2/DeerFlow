"""Task-scoped acquisition intent, separate from permission and local processing."""

import hashlib
import re
from collections.abc import Mapping

from langchain_core.messages import ToolMessage

from deerflow.config.datalake_tools import RETIRED_DATALAKE_TOOL_NAMES
from deerflow.utils.messages import get_original_user_content_text, is_real_user_message

DATA_TOOLS = frozenset({"datalake_smart_qa", "datalake_data_qa"}) | RETIRED_DATALAKE_TOOL_NAMES
STATE_KEY = "datalake_query_scope"
ACCESS_HINT = "请先点击数据湖按钮开启权限后再查询。可以继续处理已有的本地文件；不会使用外部查询替代数据湖查询。"
SOURCE_HINT = "企业数据只能从获准的数据湖来源获取。查询失败或为空须如实说明；可用 process_local_data 处理已获准的结果或当前对话上传文件，不能改用外部来源、任意脚本或编造数据。"
LOCAL_TOOLS = frozenset({"ask_clarification", "think", "write_todos", "read_file", "write_file", "str_replace", "ls", "glob", "grep", "view_image", "present_files", "task", "process_local_data"})
# These controls only record stage checkpoints. Their own controller still
# enforces the server canary, Skill ownership, receipts and budgets; they grant
# no data access and cannot execute code or make external requests.
STAGE_CONTROL_TOOLS = frozenset({"start_execution_stages", "advance_execution_stage"})
ALLOWED_TOOLS = DATA_TOOLS | LOCAL_TOOLS | STAGE_CONTROL_TOOLS
_QUERY = re.compile(
    r"查询|查一下|查下|查找|检索|统计|汇总|筛选|调取|提取|排名|列出|找出|获取|问数|有哪些|"
    r"是多少|有多少|最大|最小|最高|最低|\b(?:query|retrieve|search|fetch)\b", re.I
)
_SOURCE = re.compile(r"数据湖|data\s*lake|(?:内部|企业|公司)(?:数据库|数据表)|(?:内部|企业内部|公司内部)业务数据", re.I)
_EXPLANATION = re.compile(
    r"^(?:请|帮我|能否|麻烦|一下|\s)*(?:介绍|解释|讲解|说明|分析.*(?:代码|报错|"
    r"故障)|调试|修复|审查|讨论|什么是|为什么)|(?:是什么|代码如何|工具怎么|报错原因|故障原因|查询原理)", re.I
)
_FOLLOWUP = re.compile(
    r"^(?:请|帮我|麻烦|\s)*(?:继续|重试|已开启|权限已开启|再查一下|根据(?:刚才|上述|这些|查询)(?:的)?(?:结果|数据)|"
    r"把(?:刚才|上述|这些|查询)(?:的)?(?:结果|数据)|(?:分析|整理|处理|导出)(?:刚才|上述|这些|查询)?(?:的)?(?:结果|数据))",
    re.I,
)
_NEW_TASK = re.compile(r"换个|新任务|新的任务|另一个|另一篇|不再|取消查询|改为|改做", re.I)
_RESULT_REFERENCE = re.compile(r"(?:刚才|上述|这些|查询)(?:的)?(?:结果|数据)|数据湖(?:结果|返回的数据)", re.I)
_PROCESSING = re.compile(r"生成|导出|绘图|制图|排版|Word|Excel|图表|整理结果|分析结果", re.I)
# Intent comes from the original human request, not model-generated tool args.
_BUSINESS_QUERY = re.compile(
    r"查阅|(?:^|[，。；\s]|帮我|请|麻烦)查|查询|查一下|查下|查查|查找|检索|统计|汇总|筛选|调取|提取|排名|列出|列一下|找出|获取|问数|有哪些|多少|几[个口座家]|属于|归属|隶属|在哪|哪个|"
    r"哪些|是多少|怎么样|如何|对比|比较|查看|看看|看一下|给我|提供|最大|最小|最高|最低|\b(?:query|retrieve|search|fetch|list|count|compare|how many)\b", re.I
)
_BUSINESS_OBJECT = re.compile(r"公司|作业区|油气田|油田|气田|区块|平台|油井|气井|井号|井名|[\w-]+井|\b[A-Z]{1,12}\d+(?:[-_]\d+)+[A-Z]?\b", re.I)
_BUSINESS_FACT = re.compile(
    r"主数据|油气田|油田|气田|区块|井|平台|下属单位|分公司|所属公司|产量|产能|产气|产油|日产|月产|年产|指标|明细|记录|列表|清单|台账|"
    r"排名|成本|储量|库存|预算|计划|实际|完成率|达成率|进度|数量|含水率|注水|井深|坐标|报告|井史|地质|完井|测井|试油|资料|文档|所属|归属|隶属", re.I
)
_DOMAIN_DOCUMENT = re.compile(r"井史|井身结构|钻井报告|完井报告|测井报告|试油报告|地质报告|生产日报|生产月报", re.I)
_LOCAL_INPUT = re.compile(r"上传|附件|这份文件|这个文件|这张表|本地文件|这份表格|这个表格|这份Excel|这个Excel|提供的表格", re.I)
_WRITING_REQUEST = re.compile(r"^(?:请|帮我|麻烦|能否|\s)*(?:写|撰写|编写|润色|改写|翻译|修改|设计|生成)", re.I)
_TEXT_OR_SYNTHETIC = re.compile(r"翻译|润色|改写|措辞|语法|这句话|模拟|虚构|示例数据|测试数据|模板|写代码|编写.*代码", re.I)
_ACQUIRE = re.compile(r"查阅|(?:^|[，。；\s]|帮我|请|麻烦)查|查询|查一下|查下|查查|查找|检索|调取|获取|问数", re.I)
_NEGATED_QUERY = re.compile(
    r"不(?:要|必|用|需要)(?:再|去|从)?(?:查询|检索|查|调取|获取)|"
    r"(?:不要|不用|无需|禁止)(?:使用|调用|访问)?(?:数据湖|data\s*lake)", re.I
)
_PUBLIC_REQUEST = re.compile(r"公开资料|公开数据|公开报道|新闻|维基|百度|(?:网上|联网)(?:查询|检索|搜索)|全球|世界|全国", re.I)


def _current_instruction(text: str) -> str:
    text = re.sub(r"```[\s\S]*?```|`[^`]*`", "", text)
    text = re.sub(r"(?m)^\s*>.*$", "", text)
    return re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"', "", text).strip()


def explicit_data_question(text: str) -> bool:
    """Recognize enterprise fact requests independently of authorization.

    Retain the legacy public name; a literal DataLake source phrase is no longer
    necessary. This bounded classifier does not claim universal semantic routing.
    """
    text = _current_instruction(text)
    if _EXPLANATION.search(text) or _NEGATED_QUERY.search(text):
        return False
    if _LOCAL_INPUT.search(text) and not re.search(r"(?:从|查询|检索|获取|调取|统计)\s*(?:企业)?数据湖|数据湖(?:中|里|内|的)", text):
        return False
    if _QUERY.search(text) and _SOURCE.search(text):
        return True
    if _PUBLIC_REQUEST.search(text):
        return False
    if (_WRITING_REQUEST.search(text) or _TEXT_OR_SYNTHETIC.search(text)) and not _ACQUIRE.search(text):
        return False
    return bool(_BUSINESS_QUERY.search(text) and
                (_DOMAIN_DOCUMENT.search(text) or
                 _BUSINESS_OBJECT.search(text) and _BUSINESS_FACT.search(text)))


def merge_query_scope(left, right):
    """Only model/before-agent hooks write scope; tool denials never taint it."""
    return right if right is not None else left


def query_scope(state, context=None):
    state = state or {}
    context = context if isinstance(context, Mapping) else {}
    messages = state.get("messages") or []
    index = next((i for i in reversed(range(len(messages))) if is_real_user_message(messages[i])), None)
    user = messages[index] if index is not None else None
    text = get_original_user_content_text(user.content, user.additional_kwargs) if user is not None else ""
    clean = _current_instruction(text)
    user_key = str(getattr(user, "id", "") or "") + text
    run_key = str(context.get("run_id") or hashlib.sha256(user_key.encode()).hexdigest())
    previous = state.get(STATE_KEY) or {}
    explicit = explicit_data_question(text)
    inherited = context.get("datalake_query_only") is True
    result_reference = bool(_RESULT_REFERENCE.search(clean))
    same_task = result_reference or bool(_FOLLOWUP.search(clean)) and not _NEW_TASK.search(clean)
    continuation = same_task and previous.get("required") is True and (previous.get("phase") != "complete" or result_reference)
    required = explicit or inherited or continuation
    trigger = "current_request" if explicit else "parent_task" if inherited else "pending_task" if continuation else "ordinary_task"
    phase = "acquisition" if required else "ordinary"
    if continuation:
        phase = previous.get("phase", "acquisition")
        if _PROCESSING.search(clean) or phase == "complete":
            phase = "processing"
    if previous.get("run_key") == run_key and previous.get("required") is True:
        required = True
        phase = previous.get("phase", phase)
        trigger = previous.get("trigger", trigger)
    # Tool attempts alone do not establish intent or authorization.
    outcome = previous.get("outcome") if continuation or previous.get("run_key") == run_key else None
    if required:
        for message in messages[(index + 1) if index is not None else len(messages) :]:
            if not isinstance(message, ToolMessage):
                continue
            if message.name in DATA_TOOLS:
                phase = "processing"
                outcome = "error" if message.status == "error" else "returned"
            elif message.name == "present_files" and message.status != "error":
                phase = "complete"
    return {"run_key": run_key, "required": bool(required), "phase": phase, "trigger": trigger, "outcome": outcome}
