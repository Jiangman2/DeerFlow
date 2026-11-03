"""Shared default language policy for model-facing agent prompts."""

DEFAULT_CHINESE_LANGUAGE_GUIDANCE = "默认使用简体中文；除非用户在当前请求中明确要求其他语言，否则所有面向用户或父智能体的回答、标题、描述、摘要和报告正文都使用简体中文。代码、命令、API 字段、文件路径、标准名称和专有名词可按需要保留原文。"


def ensure_default_chinese_language(prompt: str) -> str:
    """Append the platform language policy exactly once."""
    if DEFAULT_CHINESE_LANGUAGE_GUIDANCE in prompt:
        return prompt
    return f"{prompt.rstrip()}\n\n<language_policy>\n{DEFAULT_CHINESE_LANGUAGE_GUIDANCE}\n</language_policy>"
