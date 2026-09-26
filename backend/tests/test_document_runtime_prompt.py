from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from deerflow.agents.lead_agent import prompt as prompt_module


def test_file_guidance_distinguishes_platform_read_tool_and_pdf_command(monkeypatch) -> None:
    config = SimpleNamespace(
        sandbox=SimpleNamespace(mounts=[]),
        skills=SimpleNamespace(
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
            get_skills_path=lambda: Path("/tmp/skills"),
        ),
        skill_evolution=SimpleNamespace(enabled=False),
        tool_search=SimpleNamespace(enabled=False),
        memory=SimpleNamespace(enabled=False, injection_enabled=True, max_injection_tokens=2000),
        acp_agents={},
    )
    monkeypatch.setattr(prompt_module, "_get_enabled_skills", lambda: [])
    monkeypatch.setattr(prompt_module, "get_deferred_tools_prompt_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_build_acp_section", lambda **kwargs: "")
    monkeypatch.setattr(prompt_module, "_get_memory_context", lambda agent_name=None, **kwargs: "")
    monkeypatch.setattr(prompt_module, "get_agent_soul", lambda agent_name=None: "")

    prompt = prompt_module.apply_prompt_template(app_config=config)

    assert "`read_file` is a DeerFlow tool call, not a command installed inside the Sandbox shell" in prompt
    assert "deerflow-markdown-to-pdf INPUT.md OUTPUT.pdf" in prompt
