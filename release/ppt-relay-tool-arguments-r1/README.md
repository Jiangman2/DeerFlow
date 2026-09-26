# PPT Master：企业 DeepSeek 工具参数校验补丁候选 R1

这是独立候选源码输入包，不是已完成生产验收的热更镜像，也不含 rollout 脚本。
不得把静态/模拟测试 PASS 当成真实模型、ARM64 镜像或生产功能 PASS。

## 故障与范围

截图中同一任务先执行了真实 Bash 并启动 Confirm UI，然后报工具调用缺少 `cmd`、
`Enterprise DeepSeek returned malformed tool arguments`。现有中继只校验 JSON 对象，
未校验调用方声明的参数 schema；这个缺口已用本地测试复现。
截图不能单独证明后续 malformed JSON 是模型生成还是上游流式传输造成。

本补丁仅替换 Gateway 的 `app/gateway/codex_responses_adapter.py`：

- 对完整一批工具调用先校验 JSON 和原始参数 schema，失败整批不发布。
- JSON 截断、缺参、类型不符最多让模型重新生成两次；不猜测或改写命令。
- 已经执行的历史保留，不自动重复执行 shell/导出/发布操作。
- 使用标准 function-call arguments 事件；不发送未通过校验的半条调用。
- 合并上游已报告的纠正尝试 token 用量；最终失败也携带 usage，但前端是否展示失败用量需单独验收。
- 单次中继请求含纠正共用最多 540 秒总时限，仍可能有 Codex 客户端自己的重连。
- schema 引用仅在内存本地解析，不联网；不关闭 TLS，不扩大密钥范围。

不修改普通 Sandbox、Agent Host、PPT Master v6.6.0 技能包、Frontend、Provisioner、
渠道/PDF 功能、数字员工功能、数据库和共享文件。不会切换模型。

## 第一步：只读核对

在 datax-01 root 下执行 `python3 collect-runtime.py --output /home/cnooc/ppt-relay-runtime-时间戳.json`。
输出新文件且不覆盖旧文件；兼容宿主 Python 3.7。只读部署、Pod、源码摘要、依赖版本，
日志仅统计固定错误信号，只输出模型名称，不输出用户命令、代理/凭据环境变量值、Token 或 Secret。
失败会话：636f2a5a-3a85-4885-9df1-cd50179580ba。

预期 Gateway 为 channel-pdf-preview-r2；预期源码摘要来自已交付 R16 原始文件，
还必须通过本次运行时审计确认，不能只凭镜像标签推断。
如果不匹配，必须审核差异，禁止直接覆盖。

## 尚未放行的步骤

1. 核对真实运行时审计、源码挂载和 Gateway 上已有修复，冻结镜像基线。
2. datax-01 无 Gateway Pod 时，用 /opt 下独立、CPU/内存/I/O/并发受限的 BuildKit
   和本地 OCI 基线构建；本包所有 RUN 均 network=none，不在线装包，不在生产 Pod 中运行 apply-candidate.py。
3. 在基线 ARM64 镜像跑离线 smoke 和完整候选检查，再用隔离候选完成真实企业模型工具往返。
4. 核对候选/回退镜像预装、活动任务与健康，再单独安排 Gateway 维护切换。
5. 验收普通对话、已有渠道/PDF能力以及这次同项目的 PPT 模板选择和生成。

不要用 `kubectl cp` 覆盖生产源码，不要用 `rollout restart` 试错，不要删除 Sandbox 或线程目录。
失败任务不会因装入补丁自动恢复；只有确认原 Agent 已停止且历史任务结果后，再继续同一会话。

依据：OpenAI Function calling 文档的 schema 定义和完整流式参数约定：
https://developers.openai.com/api/docs/guides/function-calling
这只是协议依据，不是企业 DeepSeek 支持 strict decoding 的证明；补丁不假设该能力。
