# V4.1 流完整性候选修复：只准备源码

基于服务器数据湖提交 `8bba320ecc6574b521d797e6ce678d2fbc46f8c1`，保留
data_qa / smart_qa 自然问法改动。执行 RUN-SOURCE.sh 创建新的独立 worktree、
运行原有测试与新增回归、提交源码；不构建、不加载镜像、不更新集群、不推送 GitHub。

线上证据：2026-10-31 11:59:42 和 12:00:36（北京时间）同一 run 有两次
新的空响应，fallback 与第二次消息 ID 一致。后续 run 正常。旧错误跨轮次
重复记录与本次真实空响应要分别判断。没有历史原始 SSE 可供反推上游原因。

离线复现使用真实 SDK 与 MockTransport 的合成 SSE；旧代码会接受角色帧后 EOF，
以及 5 个推理字符后 [DONE] 且没有 finish_reason 的响应。候选在 V4.1 独立
适配器的响应级 decoder 中检查结束标记，复用已有一次请求重试；不重跑已执行工具。
使用 SDK 原有 SSE 解码器处理分片、UTF-8、CRLF，不保存原始数据或记录凭证。
完整终止后没有 usage 或没有 [DONE] 仍可接受。正常的 length/content_filter
继续交由原有安全策略处理。GLM 与其他模型不受此改动影响。

新增 LLM_INCOMPLETE_STREAM WARNING 包含 thread/run、随机 call_id、HTTP状态、
EOF/DONE边界、帧数、内容长度、工具帧数、结束标记和用量是否出现。它观测的是
SDK消费前的解码后 SSE 事件；本包没有读取线上历史原始 SSE。

本地：268 passed，10 skipped（Linux FD 专用测试在 Windows 跳过）。
服务器必须完整通过 baseline >=250、patched >=278，跳过也判为未通过。
尚未完成服务器源码测试、镜像内 QA 或真实模型验收；不能宣称上游原始根因已修复。
