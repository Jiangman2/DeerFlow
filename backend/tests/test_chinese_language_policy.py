from pathlib import Path

from deerflow.agents.language_policy import DEFAULT_CHINESE_LANGUAGE_GUIDANCE, ensure_default_chinese_language
from deerflow.agents.lead_agent import prompt as prompt_module


def test_default_language_guidance_is_chinese_and_idempotent():
    base = "保留原有业务规则。"

    localized = ensure_default_chinese_language(base)

    assert DEFAULT_CHINESE_LANGUAGE_GUIDANCE in localized
    assert localized.count(DEFAULT_CHINESE_LANGUAGE_GUIDANCE) == 1
    assert ensure_default_chinese_language(localized) == localized


def test_lead_agent_prompt_defaults_user_visible_output_to_simplified_chinese():
    assert DEFAULT_CHINESE_LANGUAGE_GUIDANCE in prompt_module.SYSTEM_PROMPT_TEMPLATE


def test_bootstrap_skill_uses_preferred_language_and_defaults_to_chinese():
    skill_path = Path(__file__).parents[2] / "skills" / "public" / "bootstrap" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")

    assert "must always be written in English" not in content
    assert "Simplified Chinese" in content
    assert "one-line description" in content
