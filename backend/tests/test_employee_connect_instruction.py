from app.gateway.routers.channel_connections import _connect_instruction


def test_work_connect_code_is_last_token():
    code = "synthetic-connection-code"
    instruction = _connect_instruction("feishu", code)
    assert instruction.endswith("/connect " + code)
    assert instruction.split()[-1] == code


def test_other_binding_channels_preserve_wording():
    for provider in ["slack", "discord", "dingtalk", "wechat", "wecom"]:
        assert _connect_instruction(provider, "synthetic-code").endswith("/connect synthetic-code。")
