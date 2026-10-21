"""Describe old references without exporting prompts, identities or secrets."""

import hashlib
import re
import stat

TOOLS = ("datalake_detail_qa", "datalake_metrics_qa", "datalake_metric_qa")
# Only these fixed descriptive tokens can be exported from a prompt. This is
# structural evidence, not a replacement for reviewing an unknown instruction.
WORDS = ("明细", "指标", "统计", "汇总", "数据湖", "问数", "查询", "检索", "调用", "工具", "知识", "必须", "优先", "禁止", "不得", "不能", "不要", "允许", "权限", "开启", "关闭", "无结果", "失败", "为空", "外部", "搜索", "回退", "兜底", "替代", "合并", "只读", "历史", "废弃", "停用", "不再", "字段", "参数", "表", "井")
FIELDS = ("tool_groups", "tools", "allowed_tools", "excluded_tools", "disabled_tools", "deny_tools", "allow_tools", "name", "use", "description", "system_prompt")


def describe_reference(relative, raw, info):
    text = raw.decode("utf-8")
    matches = []
    for index, line in enumerate(text.splitlines(), 1):
        found = [name for name in TOOLS if name in line]
        if not found:
            continue
        matches.append(dict(line=index, tools=found, line_sha256=hashlib.sha256(line.encode()).hexdigest(), characters=len(line), vocabulary=[word for word in WORDS if word in line], known_fields=[field for field in FIELDS if re.search(r"(?<![A-Za-z0-9_])" + field + r"(?![A-Za-z0-9_])", line)], has_code_fence="```" in line, leading_list_marker=bool(re.match(r"\s*(?:[-*+] |\d+[.)] )", line))))
    parts = relative.parts
    scope = "personal" if parts[0] == "users" else "team" if parts[0] == "team_agents" else "legacy"
    return dict(handle=hashlib.sha256(str(relative).encode()).hexdigest(), agent_handle=hashlib.sha256(str(relative.parent).encode()).hexdigest(), scope=scope, filename=relative.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), regular_file=stat.S_ISREG(info.st_mode), links=info.st_nlink, mode=stat.S_IMODE(info.st_mode), affected_lines=len(matches), lines=matches[:24], lines_truncated=len(matches) > 24, raw_text_exported=False, paths_exported=False)


def inspect_references(root, scanner):
    details = []

    def observe(relative, raw, info):
        if len(details) >= 20:
            raise ValueError("affected_file_limit")
        details.append(describe_reference(relative, raw, info))

    result = scanner(root, observer=observe)
    result["affected_file_details"] = details
    result["context_is_fixed_vocabulary_only"] = True
    return result
