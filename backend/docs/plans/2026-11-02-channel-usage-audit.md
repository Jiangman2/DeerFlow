# IM 渠道使用审计与归因（2026-11-02）

状态：已实现（后端 + 管理端 UI），待重启 Gateway 生效。

## 问题

机器人在飞书群里被大量同事使用后，能看到的只有文本日志：

- 谁用过、用了几次、问了什么，没有结构化记录，无法按人或按时间回溯；
- 被拒绝（身份未绑定）和被丢弃（群里没 @ 机器人）的消息完全无痕——后者发生在 adapter
  内部，`ChannelManager` 根本看不到；
- 出问题（run 失败、平台报错）时只能翻 `gateway.log`，没有办法把一段时间的失败样本
  一次性交给模型做根因分析；
- 无法把使用情况和错误主动汇报给负责人。

## 目标

1. 记录所有渠道操作，可按人 / 时间窗 / 事件类型 / 结果 / 关键字筛选；
2. 把筛选结果摘要推送到飞书；
3. 让 agent 读取筛选出的失败样本，还原报错原因。

## 设计

四层，各层职责不重叠：

```
渠道层 (app/channels/*)
  └─ audit.py           构建 audit_logs 载荷，fire-and-forget 落库
harness  (packages/harness/deerflow/persistence/observability/sql.py)
  └─ list_audit_logs / audit_summary   通用过滤 + 窗口聚合
应用层  (app/channels/*)
  ├─ audit_report.py    markdown 摘要渲染 + 飞书投递
  ├─ audit_analysis.py  一次性 LLM 归因（不建 thread / 不开 run）
  └─ audit_digest.py    周期日报的独立 asyncio 循环
管理端  (app/gateway/routers/observability.py)
  └─ GET /channel-audit · POST /channel-audit/send · POST /channel-audit/analyze
```

### 关键取舍

**采集放在渠道层，不放在全局 HTTP 中间件。** 仓库里没有全量请求审计中间件，所有
`audit_logs` 行都是 handler 显式写的。为这个需求新加一个全局中间件会改变既有端点
的审计语义（还会把健康检查、静态资源一起记进去），所以沿用"逐点选择加入"。

**被丢弃的消息必须在 adapter 内记录。** `mention_only_in_group` 的 drop 发生在
`feishu.py` 里、`_pending_connect_code()` 之前，直接 `return`。如果不在那里记
`channel.message.skipped`，这条路径永远不会出现在任何日志里。

**聚合写在 harness，但不叫"channel 聚合"。** `audit_summary()` 接受 `resource_type`
和 `actions` 过滤，本身对任何审计命名空间都成立；渠道只是它的第一个消费者。这样
harness 不需要知道"机器人"这个产品概念。

**聚合在 Python 里做。** SQLite 和 PostgreSQL 都要跑。可移植的分桶 SQL 需要方言
分支（`date_trunc` vs `strftime`），而窗口内行数上限由调用方控制（默认 5000，硬顶
20000），Python 聚合的代价可以接受。

**写入 fire-and-forget。** IM 消息路径不允许等 SQLite 提交。`schedule_channel_audit()`
用 `asyncio.create_task` + 强引用集合，异常只记日志；测试和关机用
`drain_channel_audit_tasks()` 收口。

**归因不建 run。** `audit_analysis.py` 走 `run_oneshot_llm()`，不创建 thread、不写
`runs` 表。否则每次分析都会在用户侧边栏多出一个空对话，而且会占用 run 并发预算。

**周期日报不塞进 `ScheduledTaskService`。** 那个服务的准入、租约、并发预算都绑定在
run 生命周期上，服务的是"用户创建的定时任务"。日志摘要是一条不需要 agent、不需要
sandbox 的纯查询，独立循环更简单也不会互相阻塞。首轮等满一个 interval 才发，避免
重启刷屏。

**报告动作单独一个 `resource_type`。** 推送和分析写 `channel_audit`，不写 `channel`，
否则"发送日报"这个动作会立刻出现在下一次日报里。

## 数据契约

`resource_type="channel"`，`method="EVENT"`，`path="channel/{channel}/{msg_type}"`。

| action | 触发点 | 说明 |
|--------|--------|------|
| `channel.message.received` | `ChannelManager._handle_message` 入口 | 每条入站消息 |
| `channel.message.rejected` | 未绑定身份分支 | 403，`details.reason="unbound_identity"` |
| `channel.message.skipped` | `FeishuChannel` @ 门禁 drop | 204，`details.reason="mention_only_in_group"` |
| `channel.command` | `ChannelManager._handle_command` | 带 `command` 与 `reply_preview` |
| `channel.run.completed` | 分派成功 | 带 `duration_ms` |
| `channel.run.failed` | 三条 except 分支 | `error="<ExcType>: <msg>"` |

`user_id` 是 DeerFlow 用户（`msg.owner_user_id`），因此现有的按人筛选直接可用；平台
身份（`chat_id` / `platform_user_id` / `chat_type` / `connection_id` / `topic_id` /
`workspace_id`）与 `message_id` / `text_preview` 放 `details_json`。

## 投递

飞书卡片正文是单个 `markdown` 元素，`lark_md` 不渲染表格，所以摘要只用项目符号。

主动推送只能按 `chat_id` 寻址（仓库里没有 open_id / email 寻址能力）。构造
`OutboundMessage(thread_id="", ...)` 且 **不设 `thread_ts`**，才会落到
`FeishuChannel._create_card()`；`thread_ts` 非空会走 reply/patch 分支，把日报怼到
某条无关卡片下面。

收件人：`DEER_FLOW_CHANNEL_AUDIT_CHAT_ID`，或 `channels.<name>.audit_notify_chat_id`。

## 配置

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEER_FLOW_CHANNEL_AUDIT_CHAT_ID` | 空 | 推送目标 chat_id |
| `DEER_FLOW_CHANNEL_AUDIT_DIGEST_ENABLED` | `false` | 周期日报开关 |
| `DEER_FLOW_CHANNEL_AUDIT_DIGEST_INTERVAL_HOURS` | `24` | 间隔 |
| `DEER_FLOW_CHANNEL_AUDIT_DIGEST_WINDOW_HOURS` | = 间隔 | 统计窗口 |
| `DEER_FLOW_CHANNEL_AUDIT_DIGEST_TITLE` | `机器人使用日报` | 标题 |
| `DEER_FLOW_CHANNEL_AUDIT_DIGEST_CHANNEL` | `feishu` | 渠道 |

不新增 `AppConfig` 子模型：相关配置全部走环境变量与 `channels.*` 的透传 dict，
因此不需要提升 `config_version`。

## 已知边界

- 没有保留策略。`audit_logs` 只增不减（与仓库现状一致，`dashboard_rollups` 的 TTL
  不覆盖审计表）。渠道事件量级远低于 run 事件，暂不处理；需要时再加 prune 脚本。
- 记录的覆盖率是"渠道入口级"，不覆盖出站回复、表情回应、连接码绑定本身。
- `feishu.py` 的 @ 门禁只对飞书生效；WeCom / 钉钉 / Telegram 仍是"群里所有消息都回"。
- `snapshot` 里的 recent-audit 列表不按窗口过滤；渠道事件量大时会挤掉更早的行。

## 测试

`backend/tests/test_channel_audit.py`（16 例）：载荷构建、聚合、过滤、摘要渲染、
投递分支、收件人解析。回归命令：

```bash
cd backend
PYTHONPATH=packages/harness .venv/Scripts/python.exe -m pytest tests/test_channel_audit.py -q -p no:cacheprovider
```