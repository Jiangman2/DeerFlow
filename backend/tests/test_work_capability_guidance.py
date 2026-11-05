"""Only current authenticated Work runs receive the fixed capability note."""

import pytest

from deerflow.agents.lead_agent.channel_guidance import with_channel_guidance


@pytest.mark.parametrize("context", [None, {}, {"message_source": {"kind": "web"}}, {"is_internal": True, "message_source": {"kind": "internal"}}, {"is_internal": True, "message_source": {"kind": "im", "provider": "slack"}}])
def test_other_surfaces_keep_the_original_system_prompt(context):
    assert with_channel_guidance("original", context) == "original"


@pytest.mark.parametrize("available", [True, False])
def test_authenticated_work_note_uses_server_readiness_and_never_grants_authority(available):
    context = {"is_internal": True, "message_source": {"kind": "im", "provider": "feishu"}, "work_capabilities": {"available": available}}
    prompt = with_channel_guidance("original", context)
    assert prompt.startswith("original")
    assert "平台已接入" in prompt and "不是个人资源已授权的证明" in prompt
    assert ("暂不可用" in prompt) is (not available)
    assert "不得编造授权链接" in prompt


def test_a_source_label_alone_is_not_enough_to_change_prompt():
    context = {"message_source": {"kind": "im", "provider": "feishu"}, "work_capabilities": {"available": True}}
    assert with_channel_guidance("original", context) == "original"
