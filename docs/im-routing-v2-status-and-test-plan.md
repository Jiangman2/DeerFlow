# IM 路由 V2 —— 完成情况 · 人工测试计划 · 待完善清单

> 生成于 2026-11-10 10:20（Asia/Shanghai）。代码**尚未提交**，全部改动仍在工作区。
> 相关文档：[架构](im-routing-v2-architecture.md) · [ADR](im-routing-v2-adr.md) · [迁移手册](im-routing-v2-migration-runbook.md) · [验收报告](im-routing-v2-acceptance.md)

---

## 0. 一句话结论

飞书单聊/群聊的"会话归属"从「一张全局 JSON 指针表 + 内存去重 + 引用只拼文本」换成了「数据库三表 + per-owner 指针 + generation CAS + 事件幂等 + 引用真恢复或失败关闭」，**代码与单测层面已闭环（130 个测试通过，8/8 场景用真实 SQLite 跑通），但还没在真实飞书群里跑过一条端到端消息**——这正是接下来人工测试要补的最大空白。

---

## 1. 已完成清单

### 1.1 数据层（新增）

| 文件 | 内容 |
|---|---|
| [backend/packages/harness/deerflow/persistence/im_routing/model.py](../backend/packages/harness/deerflow/persistence/im_routing/model.py) | 三张表 ORM：`im_session_pointers` / `im_message_links` / `im_event_inbox` |
| [backend/packages/harness/deerflow/persistence/im_routing/sql.py](../backend/packages/harness/deerflow/persistence/im_routing/sql.py) | `ImRoutingRepository`：`get_pointer` / `insert_pointer_if_absent` / `cas_switch_pointer` / `record_link` / `get_link` / `claim_event` / `release_event` / `finish_event` |
| [backend/packages/harness/deerflow/persistence/migrations/versions/0023_im_session_routing.py](../backend/packages/harness/deerflow/persistence/migrations/versions/0023_im_session_routing.py) | Alembic 迁移，当前唯一 head |

约束要点：

- 指针作用域 = **一次性会话**：`(provider, connection_id, owner_user_id, channel_type, external_conversation_id)`，且 `external_topic_id` 恒为 `""`（飞书话题只是成员关系，不是会话边界）。
- `im_message_links` 唯一键 `(provider, connection_id, message_id)`，`direction` ∈ `inbound|outbound`，`message_kind` ∈ `message|card`。
- `im_event_inbox` 唯一键 `(provider, event_key)`，`event_key = channel:chat_id:message_id`，`status` ∈ `PROCESSING / COMPLETED / FAILED`，附带 `route_reason` 落审计。

### 1.2 决策内核（新增）

[backend/app/channels/routing_v2.py](../backend/app/channels/routing_v2.py) —— 纯函数，不碰 IO：

- 决策矩阵（`RouteDecision`）：`NORMAL` / `RESTORE` / `QUOTE_ONLY` / `REJECT` / `FAIL_CLOSED`
- 原因码（`RouteReason`）：`NORMAL`、`SELF_REPLY_RESTORE`、`OWN_QUESTION_RESTORE`、`NORMAL_QUOTE`、`CROSS_OWNER_QUOTE`、`CROSS_CHAT_REFERENCE`、`REPLY_MAPPING_MISSING`、`REPLY_SOURCE_UNKNOWN`、`THREAD_DELETED`
- 引文注入：`apply_quote(text, quote, cross_owner=...)` → `"[引用] 群里另一位同事的消息：…"` 或 `"[引用] 先前的消息：…"`

### 1.3 渠道管理集成（改动）

[backend/app/channels/manager.py](../backend/app/channels/manager.py)：

- `_handle_message` 入口先做 `_apply_effective_owner` → `im_event_inbox` 认领（重复投递直接丢弃），三个异常分支各自 `_release_routing_event` 供重投，成功才 `_finish_routing_event`。
- `_handle_routed_chat`：解析决策 → 失败则回失败文案且**不建 Run、不动指针**；`QUOTE_ONLY` 用 `apply_quote` 改写 prompt；写 inbound link；在决策指定的 thread 上跑。
- `/new` 走 `_handle_new_conversation_v2`：建新 thread + `cas_switch_pointer`（最多 5 次争用重试）。
- V1/V2 开关：`_routing_v2_active()` 需要「有 repo + 开关开 + 渠道在 `ROUTING_V2_CHANNELS`（目前只有 feishu）」三者同时成立，任一不满足整体降级回 JSON store 老路径。

### 1.4 飞书适配器（改动）

[backend/app/channels/feishu.py](../backend/app/channels/feishu.py)：

- 只把 `parent_id` 当"回复"（`root_id` 是话题成员关系，不算）。
- 拉取被引消息后写 5 个 metadata：`reply_message_id` / `reply_sender_type` / `reply_sender_id` / `reply_from_self_bot` / `quoted_text`；V2 下**不再拼引文**，交给 router 决定。
- 机器人自身 open_id 未解析时 `reply_from_self_bot = None` → 失败关闭（宁可报错也不猜）。
- 群聊（含 `chat_type == "topic"`）一律要求真实 @ 实体；带 `@名字` 的纯文本不算。
- 发出的卡片 message_id 逐一写入 `im_message_links`（`direction=outbound`），这是"引用机器人回答能恢复原线程"的前提。
- V2 下不再读写 `channel_session_store`（JSON 指针表），Web 端与飞书端指针互不干扰。

### 1.5 服务装配（改动）

[backend/app/channels/service.py](../backend/app/channels/service.py)：`_make_routing_repo()` 在 persistence 引擎就绪后构造 repo；`DEER_FLOW_ROUTE_V2_ENABLED`（默认 `1`）控制总管开关。

### 1.6 运行环境（已配好）

- [0_git_latest/config.yaml](../config.yaml)：唯一模型 `deepseek-v4.1-flash-1017`（DashScope 兼容模式，`max_tokens: 32768`，thinking 开/关两套 `extra_body`）；`channels:` 段已启用飞书。
- [0_git_latest/.env](../.env)（gitignore）：`DASHSCOPE_API_KEY`、`FEISHU_APP_ID=cli_aa3b9ac9dfb8dd25`、`FEISHU_APP_SECRET`、`FEISHU_DOMAIN=https://open.feishu.cn`。
- 切换国内外域名：[scripts/switch-feishu-domain.ps1](../scripts/switch-feishu-domain.ps1)（`.\scripts\switch-feishu-domain.cmd feishu|cnooc`）。

### 1.7 测试与验证

| 项 | 结果 |
|---|---|
| 新增测试 | 54 个：`test_routing_v2.py` 19 · `test_im_routing_repository.py` 7 · `test_channel_routing_v2.py` 14 · `test_feishu_routing_v2.py` 14 |
| 既有回归 | `test_feishu_group_isolation.py` 22（含新增 topic 用例） |
| 组合回归 | 130 passed, 1 skipped（skip 是 Postgres 参数化） |
| 真实 DB 场景脚本 | [backend/_verify_nine_scenarios.py](../backend/_verify_nine_scenarios.py) → `8/8 passed`，落库 `pointers=12 / links=16 / inbox=2` |
| 静态检查 | `ruff check .` 全绿，`ruff format --check` 无差异 |
| 迁移 | 单 head `0023_im_session_routing`，`bases=['0001_baseline']` |
| 已知平台性失败 | Windows 上 `tests/test_channels.py::TestChannelService::*` 与 `tests/blocking_io/test_channel_runtime_config_store.py`（`ModuleNotFoundError: No module named 'fcntl'`），与本次改动无关 |

---

## 2. 九个场景现状

| # | 场景 | 自动化 | 真实环境 | 说明 |
|---|---|---|---|---|
| S01 | A、B 同群同时 @ 机器人 | ✅ | ⬜ 待测 | 各自独立线程，指针互不覆盖 |
| S02 | A 在群 X / 群 Y 分别提问 | ✅ | ⬜ 待测 | `external_conversation_id` 参与路由键 |
| S03 | A 同群连续追问 | ✅ | ⬜ 待测 | 复用指针，不新建线程 |
| S04 | B 回复 A 的机器人消息 | ✅ | ⬜ 待测 | 引文（含真实内容）进入 **B 自己**的线程 |
| S05 | 同一飞书消息重复推送 | ✅ | ⬜ 待测 | inbox 幂等，只跑一次 |
| S06 | 超时/失败/网络中断 | 🟡 部分 | ⬜ 待测 | 已做：失败释放认领可重投；未做：发送侧 Outbox 与 UNCERTAIN 对账 |
| S07 | 群聊里读个人知识库 | ⏸ 延后（不实现） | — | 未写任何"群共享"代码，完全沿用 owner 作用域；**无回滚需求**，见 §4 P1 |
| S08 | 归档到 Web 会话目录 | ⛔ | — | 本轮不做；数据层 `im_message_links` 已备好 |
| S09 | `/new` 后回复旧 Thread 的机器人消息 | ✅ | ⬜ 待测 | 恢复旧线程且**不移动指针**，随后的普通消息仍回新线程 |

---

## 3. 人工测试手册

### 3.1 先把服务拉起来

昨晚两个后台进程都被系统杀掉了，当前 8001 / 3000 均无监听。启动方式（两路分离，不用 `make dev`，因为本机没有 nginx）：

```powershell
# 后端
cd F:\jupyter\0_harness_proj\6_df_basic_harness\0_git_latest\backend
$env:PYTHONPATH='.'
cmd /c ".\.venv\Scripts\python.exe -m uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 > _gw.log 2>&1"

# 前端（另开一个终端 / 后台）
cd F:\jupyter\0_harness_proj\6_df_basic_harness\0_git_latest\frontend
pnpm dev
```

就绪判据（`_gw.log` 里依次出现）：`upgrade head (0023_im_session_routing)` → `ChannelService started with 1/1 ready channels` → `[Feishu] bot open_id resolved: ou_...` → `connected to wss://msg-frontier.feishu.cn/ws/v2` → `Uvicorn running on http://127.0.0.1:8001`；前端 `http://127.0.0.1:3000` 返回 200。

> 注意：端口被占时报 `[Errno 10048]`。先 `Get-NetTCPConnection -LocalPort 8001 -State Listen` 找到 pid 再 `Stop-Process`。

### 3.2 Web 端冒烟（5 分钟）

1. 打开 `http://127.0.0.1:3000` → 应跳 `/setup` 建管理员（首启无账号）。
2. 建完登录，新建对话，发一句"你好" → 应看到流式输出与 thinking 折叠块。
3. 这一条同时验证模型链路（DashScope）与前端反代（`/api/*` → 8001）。

### 3.3 飞书端 12 步清单

每组动作后都可以用 3.5 的 SQL 对照"应有多少行"。

| 步骤 | 动作 | 预期现象 | 应落库 |
|---|---|---|---|
| 1 | 群里 **@机器人** 发「你好」 | 建新线程并回复 | 1 pointer + 1 inbound link + 1 outbound card |
| 2 | 同群再发「刚才说的再说一遍」（@） | **同一线程**继续，不新建 | pointer 行数不变、generation 不变；inbound link +1 |
| 3 | 让同事 B 在该群 **@机器人** 发「我是B」 | **独立线程**，B 看不到 A 的历史 | pointers +1（owner 不同） |
| 4 | B 长按 A 的机器人回复 →「回复」引用它，@机器人 问「这是什么意思」 | B 的线程里看到 `[引用] 群里另一位同事的消息：…` + 引文原文 | A 的 pointer generation 不变；B 的 link +1 |
| 5 | A 引用**自己机器人**的某条回答，@机器人 追问 | 回到**那条回答所属的旧线程**（不是当前线程） | pointer 的 `thread_id` 与 `generation` 都不变 |
| 6 | 接步骤 5 再发一条普通 @ 消息 | 仍回**当前**线程，不黏在旧线程 | pointer 不变 |
| 7 | 发 `/new` | 回复「已开启新的对话」类提示 | pointers 的 `generation` +1、`thread_id` 变新 |
| 8 | `/new` 之后引用**旧线程**里的机器人回答 | 恢复到旧线程，且指针仍指向新线程 | pointer 的 generation 不变（关键！） |
| 9 | 只引用机器人回答、**不 @** | 无任何回复（消息被丢弃） | 三表均无新增 |
| 10 | 在群里手动打文本 `@机器人名字 你好`（不走飞书 @ 实体） | 无回复 | 三表均无新增 |
| 11 | 群里两人交替各问 3 句 | 两条线程各自连续 | pointers=2，links 分别累加 |
| 12 | 在**话题群**（topic 模式）重复步骤 1–2 | 行为与普通群一致；话题内不会分裂出多个会话 | pointer 的 `external_topic_id` 恒为 `""` |

失败路径抽测（可选，用来确认"失败关闭"）：把某条 link 从 `im_message_links` 里删掉，再引用那条机器人回答 → 应回「没有找到对应的会话记录」且**不建 Run**。

### 3.4 观测手段

日志（`backend/_gw.log`）关键行：

```powershell
Select-String -Path backend\_gw.log -Pattern "dropping duplicate|routing|RouteReason|restore|pointer|失败|引用"
```

### 3.5 数据库对照

```powershell
cd F:\jupyter\0_harness_proj\6_df_basic_harness\0_git_latest\backend
.\.venv\Scripts\python.exe -c @"
import sqlite3, json
c = sqlite3.connect(r'.deer-flow\data\deerflow.db')
for row in c.execute('select owner_user_id, external_conversation_id, thread_id, generation from im_session_pointers'):
    print('PTR', row)
for row in c.execute('select message_id, direction, message_kind, thread_id, sender_external_id from im_message_links order by id desc limit 8'):
    print('LINK', row)
for row in c.execute('select event_key, status, route_reason, thread_id from im_event_inbox order by id desc limit 8'):
    print('EVT', row)
"@
```

判读要点：

- `im_session_pointers`：**每个 (owner, 群) 只有一行**；`generation` 只在 `/new` 时递增。
- `im_message_links`：机器人每条卡片都应有 `direction=outbound` 的行，否则"引用回答恢复线程"必然失败。
- `im_event_inbox`：`status` 应为 `COMPLETED`；长期停在 `PROCESSING` 说明某次 run 崩了没释放。

---

## 4. 已知不够好的地方（按优先级）

### P0 —— 影响"能不能放心用"

| 问题 | 现状 | 建议 |
|---|---|---|
| **发送侧没有 Outbox** | 卡片发送失败/超时后，平台侧是否已投递无法判定（S06 的 UNCERTAIN 态）；`im_message_links` 只记录"发出去了"的卡片 | 下一轮加 outbox 表 + 对账任务：发送前落 `PENDING`，确认后 `SENT`，超时进 `UNCERTAIN` 并提示用户 |
| **单实例假设** | 进程内 `serial_state.lock` 只保证单进程串行；多副本部署时同一 thread 可能并发 | 已有 `im_event_inbox` 幂等兜底，但线程级串行还需跨进程锁（DB advisory lock 或队列） |
| **失败文案不可配置** | 错误码 → 中文文案硬编码在 `routing_v2.py` | 抽到配置，便于运营调整话术 |

### P1 —— 体验与语义

| 问题 | 现状 | 建议 |
|---|---|---|
| **群聊知识库权限（S07）** | **按 2026-11-10 决策延后，本轮不做。** V2 没有引入任何"群共享知识库"代码：`git diff` 未触及 `memory/`、`knowledge/` 等模块，`storage_user_id` 仍取发消息者本人（`_channel_storage_user_id(msg)`，`backend/app/channels/manager.py:570`），与 V1 行为一致 → **因此不存在"回滚"动作** | 将来要做时再定义：群 → 绑定一个"群工作区"，成员读写该工作区；需要产品决策。若要把整体路由实验退回 V1，用 `DEER_FLOW_ROUTE_V2_ENABLED=0` 即可 |
| **Web 归档（S08）** | 不做 | `im_message_links` 已能查出某 thread 的全部消息，接 `/new` 时写一份摘要到 Web 会话即可 |
| **`_resolve_available_skill_names`** | V2 下仍读 JSON store，取不到 → 返回 `None` → 用默认技能集 | 改成从 thread metadata 读，或接受"V2 暂时只有默认技能" |

### P2 —— 长期/一致性

| 问题 | 现状 | 建议 |
|---|---|---|
| **V1 数据不迁移** | 老 JSON 指针不导入新表 | 一次性脚本：读到 group/owner 就 `insert_pointer_if_absent`，或干脆让老会话"从下一条消息起变成新线程"（文档需写明） |
| **迁移 0023 撞号风险** | 上游 `backend/AGENTS.md` 曾把 "migration 0023" 预留给 Work grant hooks | 合入 Work 时顺延为 0024 |
| **引用内容未脱敏（E28）** | 引文原样注入 prompt | 接合规要求后再加字段级脱敏 |
| **Windows 平台测试失败** | `fcntl` 相关 5 个用例 | 非本次问题；CI 用 Linux 即绿 |

---

## 5. 建议的下一步顺序

1. **先做 3.2 + 3.3 的步骤 1–3**（最基础的三条），确认"能通、线程不串"。
2. 再做步骤 4–8（引用与 `/new` 的语义），这是本次重构的核心收益，也是最容易出细节问题的部分。
3. 最后做步骤 9–12（丢弃规则与话题群）与失败路径抽测。
4. 测完再决定 P0 里先做 Outbox 还是先做多实例锁——两者都依赖"真实流量长什么样"。

测试中任何一步不符合预期，把 `_gw.log` 相关片段 + 3.5 的查询输出发我，我按 `route_reason` 直接定位是决策错、落库错还是适配器解析错。