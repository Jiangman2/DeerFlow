# 飞书 × DeerFlow 分群会话路由 V2：架构审计与设计

对应需求：`1-开发文档/飞书 × DeerFlow 企业级分群会话路由系统.md`（V2.1）。
本轮范围（与需求方确认的 A 方案）：**路由内核**——会话指针与 generation、`/new`
幂等与原子切换、历史引用恢复与失败关闭、跨用户/跨群/跨机器人判定、平台消息映射、
数据库迁移、单元测试、本文档与 ADR/Runbook/验收报告。Web 群目录（M3）与
Outbox 状态机（M4 的一部分）不在本轮。

## 1. 现状审计（V1）

审计对象：`backend/app/channels/`（`manager.py`、`feishu.py`、`store.py`）与
`backend/packages/harness/deerflow/persistence/channel_connections/`。

| # | V1 行为 | 与需求冲突 | 影响 |
| - | - | - | - |
| A1 | 线程指针存在 JSON 文件 `store.json`，键为 `channel:chat_id[:topic_id]`，值为单个 `thread_id` | 无 owner 维度，同一群所有成员共用一个键 | 需求要求“同群不同用户独立指针”；V1 只能靠 `topic_id` 前缀 `<open_id>:` 模拟，且知识库/技能解析路径读的是无 owner 的键 |
| A2 | 指针无 generation、无 CAS | `/new` 直接覆盖 `thread_id` | 并发重复投递的 `/new` 可产生多个线程；无法做乐观锁 |
| A3 | 去重只有进程内 `OrderedDict`（`_prepared_inbound` / `_inbound_dedupe_key`） | 多副本/重启后失效 | 同一条飞书事件可能被处理两次 |
| A4 | 群 @ 门禁只判断 `chat_type == "group"` | 飞书话题群的 `chat_type == "topic"` 不校验 @ | 话题群里任何消息都会触发 Agent |
| A5 | 发送者隔离同样只用于 `chat_type == "group"` | 话题群不隔离 | 话题群同群多人共享线程 |
| A6 | 引用恢复靠适配器把引用文本拼进 prompt（`[引用] 本机器人先前的回复：…`） | 只是“把文本带过去”，从不恢复原 Thread；也无法失败关闭 | 用户以为在续前文，实际是当前 Thread + 一段孤立文本 |
| A7 | “是否本机器人”用 `sender.sender_type == "app"` 判定 | 任何 app 消息都被当成自己 | 引用别的机器人（或其他租户的 DeerFlow）会被错误恢复 |
| A8 | 出站卡片 message_id 只在流式路径（running card）写回 store | 非流式卡片、fallback 卡片无映射 | 回复这些卡片时无法定位原 Thread |
| A9 | `_remember_thread_mapping` 只写 JSON store，与 manager 的 SQL `conversation` 读写不一致 | 两套指针来源不一致 | SQL 模式下 V1 适配器写入的映射 manager 读不到 |

结论：V1 具备“群聊必须结构化 @”“按发送者隔离（仅 group）”“引用文本带入”三项能力，
但缺少需求强制的路由键、指针幂等、历史恢复、失败关闭与跨域判定。

## 2. 目标路由键

```
(provider, connection_id, owner_user_id, channel_type, external_conversation_id)
```

- `provider`：`feishu`（V2 目前只对 `ROUTING_V2_CHANNELS = {"feishu"}` 生效）。
- `connection_id`：用户连接记录 id；未绑定连接时为空串（与 `channel_connections`
  的既有语义一致）。
- `owner_user_id`：DeerFlow 所有者（`ChannelManager._effective_owner_user_id`）。
  这是“同群不同用户互相隔离”的唯一依据，**不是**平台 open_id。
- `channel_type`：`group`（含飞书 `topic` 话题群）或 `private`。
- `external_conversation_id`：飞书 `chat_id`（私聊是会话 chat_id）。
- `external_topic_id` 固定为 `""`：**指针作用域是“群”，不是飞书话题**。飞书话题的
  `root_id`/`thread_id` 只表示“这条消息属于哪个话题”，不表示“另一个会话”；
  把它纳入键会让同一群分裂出多个指针（`im_session_pointers` 的查询固定带
  `external_topic_id == ""`）。

## 3. 数据模型

新增三张表（`deerflow.persistence.im_routing`，迁移 `0023_im_session_routing`）：

### `im_session_pointers` — 当前会话指针

| 列 | 说明 |
| - | - |
| `id` | uuid hex |
| `provider` / `connection_id` / `owner_user_id` / `channel_type` | 路由键 |
| `external_conversation_id` | 飞书 chat_id |
| `external_topic_id` | 恒为 `""`（见 §2） |
| `thread_id` | 当前 Thread |
| `generation` | 乐观锁版本，`/new` 成功后 +1 |
| `created_at` / `updated_at` | |

唯一约束 `uq_im_session_pointer_scope(connection_id, owner_user_id, channel_type,
external_conversation_id, external_topic_id)`：一个 owner 在一个群里只能有一个当前指针，
由数据库而不是进程锁保证。

### `im_message_links` — 平台消息 ↔ Thread 映射

入站消息与出站卡片都记录（`direction=inbound|outbound`，`message_kind=message|card`），
含 `owner_user_id`、`thread_id`、`sender_external_id`、`root_message_id`、
`parent_message_id`、`task_id`。唯一键 `(provider, message_id)` 使重复投递幂等，
`idx_im_message_links_thread(thread_id, created_at)` 支撑按 Thread 回溯。

### `im_event_inbox` — 事件幂等

`(provider, event_key)` 唯一，`event_key = "<channel>:<chat_id>:<message_id>"`。
状态 `PROCESSING → COMPLETED`；处理异常时记录被删除（`release_event`），使飞书重投可以
重新处理；正常完成则保留记录，重投直接丢弃。这是跨进程/跨重启的幂等层，取代 V1 纯内存去重。

### 与需求建议表的映射

| 需求建议表 | 本轮状态 |
| - | - |
| `im_session_pointers` | ✅ 已实现 |
| `im_message_links` | ✅ 已实现 |
| `im_event_inbox` | ✅ 已实现（逻辑消息唯一键 + 幂等） |
| `im_thread_registry` | ⛔ 由 Gateway `threads` 表承担；V2 通过 `client.threads.get` 校验存在性（失败即 `THREAD_DELETED`），未新建冗余表 |
| `im_message_tasks` | ⛔ 本轮不做任务状态机（M4），当前以 `im_event_inbox` + Run 归属表达 |
| `im_delivery_outbox` | ⛔ 下一轮（UNCERTAIN/SENT 对账） |
| `im_route_audit` | 🟡 部分：路由原因写入 `im_event_inbox.route_reason`；独立审计表留待 M4 |
| `im_user_bindings` / `im_group_catalog` | ⛔ 绑定沿用 `channel_connections`；群目录属 M3（Web） |

说明：需求文档允许“等价功能/唯一约束”，因此不复刻同名表。

## 4. 路由决策

纯函数 `app/channels/routing_v2.py::resolve_route()`（无 IO，可单测）输入
`ReplyRef`（引用消息的 id/发送者类型/发送者 id/是否本机器人）与 `MessageLink`
（已持久化的映射），输出 `RouteDecision(action, reason, thread_id, error_code,
quote_text)`：

| 场景 | action | reason |
| - | - | - |
| 不是回复 | `CURRENT` | `NORMAL` |
| 回复普通成员的消息 | `QUOTE_ONLY` | `NORMAL_QUOTE` |
| 回复本人旧提问（有同群同 owner 的 inbound 映射） | `RESTORE` | `OWN_QUESTION_RESTORE` |
| 回复本机器人的回复（同群同 owner） | `RESTORE` | `SELF_REPLY_RESTORE` |
| 回复同群其他 owner 的本机器人回复 | `QUOTE_ONLY` | `CROSS_OWNER_QUOTE` |
| 回复其他群的任何消息 | `REJECT` | `CROSS_CHAT_REFERENCE` |
| 引用方无法解析/身份未知 | `FAIL_CLOSED` | `REPLY_SOURCE_UNKNOWN` |
| 引用的是别的机器人 | `REJECT` | `REPLY_SOURCE_UNKNOWN` |
| 确认是本机器人消息但映射缺失 | `FAIL_CLOSED` | `REPLY_MAPPING_MISSING` |
| 映射存在但 Thread 已删除（manager 二次校验） | `FAIL_CLOSED` | `THREAD_DELETED` |

`REJECT` 与 `FAIL_CLOSED` 都**不创建 Run、不修改指针**，只回一条明确文案
（`failure_message()`）。`OWNER_MISMATCH` 文案已就绪，保留给后续“按 owner 硬拒绝”
策略；当前跨 owner 引用走 `CROSS_OWNER_QUOTE`（需求允许的“公开文本进入 B 自己的会话”）。

### 优先级

`ChannelManager._handle_message` 的顺序：事件幂等 → 绑定身份校验 →（Feishu 适配器侧
已完成 @ 门禁与命令分类）→ 命令（`/new` > 其它）→ 历史回复判定 → 当前指针。
因此 `@机器人 /new` 同时引用旧消息时，`/new` 优先（E13/E41）；群聊无结构化 @ 的消息
在适配器就被丢弃，不会走到这里（E31/E32/E33）。

## 5. 并发与一致性

- **同 owner + 同 Thread 串行**：沿用 `CHANNEL_RUN_POLICY.serialize_thread_runs` 的
  `asyncio.Lock`（进程内）＋ Gateway `multitask_strategy="reject"`（跨进程兜底）。
- **指针创建**：`insert_pointer_if_absent` 依赖唯一约束；输的一方丢弃自己刚建的线程并
  采用 winner 的指针（对应 E14）。
- **`/new` 切换**：`cas_switch_pointer(expected_generation)`，失败即重读重试，最多 5 次，
  仍失败则记 WARNING 并保持原指针（不会静默覆盖别人的切换）。
- **重复投递**：`im_event_inbox` 唯一键；处理中异常会删除记录以便重投。
- **多实例**：所有指针/幂等状态在数据库，进程内锁只用于减少同实例抖动，不作为唯一
  安全手段（区别于 V1 的 `_thread_create_locks` 唯一化）。

已知残留：`_thread_create_locks` 仍是进程内锁；多实例下“创建线程输了”的一方会留下一个
未被引用的空 Thread（无指针指向，不影响路由）。这是可接受的代价，已记录为后续清理项。

## 6. 开关与灰度

| 开关 | 位置 | 默认 | 说明 |
| - | - | - | - |
| `DEER_FLOW_ROUTE_V2_ENABLED` | 环境变量 | `1`（开；`0/false/no/off` 关） | `ChannelService._route_v2_enabled_from_env()` |
| `route_v2_enabled` | 每个渠道的运行时 config | 见上 | `ChannelService._start_channel` 只在 `name in ROUTING_V2_CHANNELS` 时置真 |
| `routing_repo` | 渠道 config | 无 DB 时为 `None` | 没有 repository 时 V2 自动降级为 V1 行为 |

降级路径（任一处不满足即回到 V1，不会半开）：`routing_repo is None`、渠道不在
`ROUTING_V2_CHANNELS`、或 `route_v2_enabled=False`。此时 `_routing_v2_active()` 为假：
指针读写仍走 JSON store、引用文本仍由适配器预置、`/new` 仍走旧的覆盖式实现。

`_resolve_available_skill_names()` 在 V2 下仍读 JSON store（该键在 V2 下不再写入），
因此返回 `None` → 使用默认技能集。这是已知限制：技能可见性不受路由改造影响，但
“按会话恢复上次技能选择”在 V2 下不生效。

## 7. 适配器契约（Feishu）

`feishu.py` 在 V2 下不再改写 prompt，只**描述**引用：

| metadata key | 含义 |
| - | - |
| `reply_message_id` | 被引用的消息 id（仅 `parent_id`，`root_id` 只是话题成员关系；自引用置 `None`） |
| `reply_sender_type` | `user` / `app` / `unknown` |
| `reply_sender_id` | 被引用消息发送者 open_id |
| `reply_from_self_bot` | `True`/`False`/`None`（`None` = 身份未知 → 失败关闭） |
| `quoted_text` | 引用消息的公开纯文本（可能为空） |

- `chat_type in {"group", "topic"}` 统一走 @ 门禁与发送者隔离（`FEISHU_GROUP_CHAT_TYPES`）。
- 出站卡片（running card / fallback card / 最终 card）都通过
  `_remember_outbound_message()` 写入 `im_message_links`，使“回复第二张卡片”也能恢复（E08）。
- V2 下 `_remember_thread_mapping()` 直接 return，避免旧 JSON 条目与 per-owner 指针分叉。

## 8. 未覆盖（下一轮）

1. Web：`Private / Group → 群 → Thread` 目录、搜索分页、同名群按 chat_id 区分（M3）。
2. Outbox：发送失败重试、`UNCERTAIN` 对账（E20）。
3. `im_message_tasks` 完整状态机与 `im_route_audit` 独立表。
4. 群内公开引用的可见性策略（E28）目前只依赖“引用文本 = 平台可见文本”，无额外脱敏。
5. V1 JSON store 历史数据不迁移：V2 下以新表为准，旧线程仍可被 Web 打开（只读历史），
   但不会被飞书指针自动指向。