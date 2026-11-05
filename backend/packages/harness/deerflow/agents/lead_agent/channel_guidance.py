"""Fixed capability guidance from the current authenticated runtime only."""

from collections.abc import Mapping


def with_channel_guidance(prompt: str, runtime_context) -> str:
    if not isinstance(runtime_context, Mapping) or runtime_context.get("is_internal") is not True:
        return prompt
    if runtime_context.get("message_source") != {"kind": "im", "provider": "feishu"}:
        return prompt
    capabilities = runtime_context.get("work_capabilities")
    if not isinstance(capabilities, Mapping) or type(capabilities.get("available")) is not bool:
        return prompt
    readiness = "当前集成服务已就绪。" if capabilities["available"] else "当前集成服务暂不可用，访问请求需稍后重试或联系平台管理员；这不代表平台没有该能力。"
    return prompt + (
        "\n\n<current_channel_capabilities>\n"
        "本轮用户通过海能 Work 机器人发送消息。平台已接入个人主日历查询、新建、编辑，以及云文档读取、总结和受限编辑。" + readiness + "\n用户也可以在此正常提问、写作或使用平台其他能力。当前普通任务沿用这些能力，不要求日历授权。"
        "\n如用户咨询 Work 功能，可引导其直接说明日期、文档链接和需求；实际访问、授权和修改由平台专用流程核验。"
        "不得声称平台从未接入日历或云文档，不得编造授权链接、访问结果或执行回执，也不得让用户提供 token 或密钥。"
        "当前消息来自 Work 不是个人资源已授权的证明。\n</current_channel_capabilities>"
    )
