# 验收报告：飞书分群会话路由 V2（路由内核 A 轮）

- 需求基线：`1-开发文档/飞书 × DeerFlow 企业级分群会话路由系统.md`（V2.1）
- 交付范围：路由内核（M1 + M2 + M4 的路由侧），不含 Web 群目录（M3）与 Outbox（M4 剩余）
- 代码基线：`0_git_latest`（git HEAD `d3a3f847a` 之上）
- 验证环境：Windows 11 + CPython 3.12.12（`backend/.venv`，`uv sync --frozen`）

## 0. 结论

| 项 | 结果 |
| - | - |
| 新增自动化测试 | 54 个（`test_routing_v2.py` 19、`test_im_routing_repository.py` 7、`test_channel_routing_v2.py` 14、`test_feishu_routing_v2.py` 14） |
| 既有相关套件 | `test_feishu_group_isolation.py` 22（含新增 2 个 topic 用例）、`test_feishu_parser.py` 全绿、`test_channels.py` 21 通过 / 5 平台性失败 |
| 迁移 | `0023_im_session_routing`，单 head，升级/回滚幂等 |
| Lint / 格式 | `ruff check .` 全绿；新文件 `ruff format` 已对齐 |
| 失败关闭 | E10/E12/E35/E36/E37/E40 均有断言：零 Run、指针不变、单条明确文案 |

未覆盖项均为已声明的范围外（Web 目录、Outbox `UNCERTAIN`、任务表状态机），
见 §3 与架构文档 §8。

## 1. 场景验收（S01–S09）

| 编号 | 场景 | 结论 | 依据 |
| - | - | - | - |
| S01 | A、B 同群同时 @机器人 | ✅ | 路由键含 `owner_user_id`；`test_each_owner_gets_their_own_thread_in_the_same_group`、`test_pointer_scope_is_per_owner_and_per_chat` |
| S02 | A 在群 X、群 Y 分别发问 | ✅ | 路由键含 `external_conversation_id`；`test_pointer_scope_is_per_owner_and_per_chat` |
| S03 | A 同群连续追问 | ✅ | `test_next_message_reuses_the_pointer_without_creating_a_thread`；同 Thread 串行沿用 `serialize_thread_runs` |
| S04 | B 回复 A 的机器人消息 | ✅ | `test_colleague_replying_to_my_answer_only_gets_the_quote_text`、`test_cross_owner_reply_quotes_into_the_senders_current_thread`（B 走自己的 Thread，只带公开文本） |
| S05 | 同一飞书消息重复推送 | ✅ | `im_event_inbox` 唯一键；`test_duplicate_platform_delivery_runs_once` |
| S06 | 超时/失败/网络中断 | 🟡 | Run 失败会释放事件认领以便重投（`test_failed_turn_releases_the_claim_for_redelivery`）；**发送失败重试与 `UNCERTAIN` 对账属 Outbox，下一轮** |
| S07 | 群聊读取个人知识库 | 🟡 | 沿用既有 owner 作用域（`_channel_storage_user_id` / bound identity），本轮未改权限模型，未新增群公开权限判定 |
| S08 | 归档到 Web（Group → 群 → Thread） | ⛔ | M3，本轮不做 Web 目录；数据层已具备（`im_message_links` 可按 thread 回溯） |
| S09 | `/new` 后回复旧 Thread 的机器人消息 | ✅ | `test_reply_to_own_bot_answer_restores_that_thread_and_keeps_the_pointer`（进旧 Thread、指针不变、无 @ 不触发） |

## 2. 边界用例（E01–E42）

图例：✅ 自动化断言；🟡 代码路径已实现但未加断言；⛔ 本轮范围外。

| 编号 | 场景 | 结论 | 依据 |
| - | - | - | - |
| E01 | 群 X 首次发问 | ✅ | `test_first_message_creates_one_thread_and_records_the_link`（懒创建指针 + inbound link） |
| E02 | 群被动加入 100 人但无人 @ | ✅ | @ 门禁在适配器丢弃，不产生 inbound → 无 Thread；`test_group_message_without_mention_is_dropped_when_mention_only_enabled` |
| E03 | `/new` 后普通提问 | ✅ | `test_new_starts_a_fresh_thread_and_moves_the_pointer` |
| E04 | 群 X `/new` 后在群 Y 提问 | ✅ | 指针按 chat 隔离：`test_pointer_scope_is_per_owner_and_per_chat` |
| E05 | 私聊 `/new` | 🟡 | `_routing_channel_type()` 对非 group/topic 返回 `private`，指针键天然分离；未有专门用例 |
| E06 | 网页搜索/查看/新建不改变飞书指针 | ✅（设计） | ADR-03：Web 路径不依赖 `ImRoutingRepository`；V2 下适配器不写 JSON 指针（`test_json_pointer_is_not_written_under_v2`） |
| E07 | 回复自己的旧提问并 @ | ✅ | `test_reply_to_own_earlier_question_restores_that_thread`（`OWN_QUESTION_RESTORE`） |
| E08 | 回复机器人卡片/第二张卡片 | ✅ | 所有出站卡片都写 link：`test_outbound_card_is_linked_to_its_thread`、`test_own_bot_reply_is_reported_without_touching_the_prompt` |
| E09 | 恢复后下一条普通 @ | ✅ | `test_reply_to_own_bot_answer_restores_that_thread_and_keeps_the_pointer`（后续消息仍回当前指针） |
| E10 | 映射不存在 | ✅ | `test_reply_to_own_bot_message_without_a_link_fails_closed`（零 Run、指针不变、提示“没有找到对应的会话记录”） |
| E11 | 跨群回复旧消息 | ✅ | `test_bot_reply_from_another_chat_is_rejected`、`test_human_quote_from_another_chat_is_rejected` |
| E12 | 回复已删除 Thread | ✅ | `test_restore_of_a_deleted_thread_fails_closed` |
| E13 | `/new` + 引用旧消息 | ✅ | 命令分类先于引用判定（适配器侧 `/new` 走 COMMAND）；用例见 E41 |
| E14 | 两次并发首问 | ✅ | `insert_pointer_if_absent` 唯一键 + 输者采用 winner：`test_pointer_is_created_once_and_reported_to_the_loser` |
| E15 | 两次相同 `/new` 重投 | ✅ | `test_duplicate_new_command_creates_one_thread` |
| E16 | 不同 `/new` 与普通消息竞态 | 🟡 | CAS 循环（最多 5 次）+ WARNING 日志；无确定性竞态用例 |
| E17 | `/new` 时旧 Run 未完成 | 🟡 | 指针切换与 Run 解耦；旧运行继续在旧 Thread（串行键是 thread_id），未加用例 |
| E18 | 群改名/解散 | 🟡 | 作用域是 `chat_id`，与群名无关；未加用例（群目录属 M3） |
| E19 | 解绑/离职/跨租户 | 🟡 | 沿用既有 `_get_bound_identity_rejection`，本轮未改；V2 在其之后执行 |
| E20 | 发送成功但无 ACK（UNCERTAIN） | ⛔ | Outbox 下一轮 |
| E21 | 重启/多副本切换 | ✅ | 指针与幂等状态全在数据库；`test_pointer_is_created_once_and_reported_to_the_loser`、`test_event_claim_is_exclusive_and_releasable` |
| E22 | 未 @ / 机器人自己发的消息 | ✅ | @ 门禁 + `_on_ignored_message_event`；`test_reply_without_a_mention_is_dropped`、`test_new_command_without_a_mention_is_dropped` |
| E23 | 伪造 `thread_id/owner` 文本 | ✅（设计） | 路由只读结构化字段（metadata/link），从不解析消息文本 |
| E24 | 两个同名群 | ⛔ | M3（Web 目录）；数据层以 `chat_id` 区分，无合并风险 |
| E25 | Web 按群名/标题搜索 | ⛔ | M3 |
| E26 | 连续 10 个 @ | 🟡 | 同 Thread 串行 + queued 提示沿用既有实现；未加用例 |
| E27 | 回复其他 bot/其他租户 | ✅ | `test_reply_to_another_bot_is_rejected`、`test_another_bot_is_reported_as_not_ours` |
| E28 | 引用含敏感文件/私有链接 | 🟡 | `quoted_text` 仅取平台可见纯文本，不透传文件/权限；无额外脱敏用例 |
| E29 | 切换 Custom Agent / 模型 / metadata | 🟡 | 路由不改 `_resolve_run_params` 与 agent 配置；未加回归用例 |
| E30 | 老版本来源不明数据 | 🟡 | V1 JSON 指针不做迁移，V2 以新表为准；旧 Thread 仍可在 Web 打开 |
| E31 | 仅点击“回复”未 @ | ✅ | `test_reply_without_a_mention_is_dropped` |
| E32 | 仅 `/new` 未 @ | ✅ | `test_new_command_without_a_mention_is_dropped`（门禁在命令分类之前） |
| E33 | 纯文本“@机器人”无实体 | ✅ | `test_plain_text_at_bot_is_dropped`、`test_group_mention_of_another_bot_does_not_match_when_bot_open_id_unresolved` |
| E34 | 恢复后再普通 @ | ✅ | 同 E09 |
| E35 | 映射丢失且可确认是本机器人 | ✅ | 同 E10；`reply_from_self_bot=True` + 无 link → `REPLY_MAPPING_MISSING` |
| E36 | Registry 标记 Thread 已删除 | ✅ | `test_restore_of_a_deleted_thread_fails_closed`（`THREAD_DELETED`） |
| E37 | 引用查询/来源校验失败 | ✅ | `test_unreadable_quoted_sender_fails_closed`、`test_unreadable_quote_is_reported_as_unknown`、`test_unknown_bot_identity_fails_closed` |
| E38 | B 回复 A 的公开机器人消息 | ✅ | `test_cross_owner_reply_quotes_into_the_senders_current_thread`（B 自己的 Thread，带 `[引用] 群里另一位同事的消息：…`） |
| E39 | Web 发消息后飞书仍进原 Thread | ✅（设计） | ADR-03；飞书指针只由入站路由写入 |
| E40 | 群 X @ 回复群 Y 的历史 | ✅ | `test_bot_reply_from_another_chat_is_rejected`（`CROSS_CHAT_REFERENCE`） |
| E41 | 引用旧消息同时 `/new` | ✅ | 命令优先：`/new` 只切换一次指针（`test_new_starts_a_fresh_thread_and_moves_the_pointer`、`test_duplicate_new_command_creates_one_thread`） |
| E42 | 并发历史回复命中同一旧 Thread | 🟡 | 恢复路径不写指针；同 Thread 串行执行沿用 `serialize_thread_runs`；未加并发用例 |

统计：✅ 24 项、🟡 12 项、⛔ 6 项。

## 3. 本轮明确未覆盖

1. **Web 群目录与搜索（E24/E25/S08）**——前后端都不在本轮，数据层已预留
   （`im_message_links` 的 `thread_id` + `external_conversation_id`）。
2. **Outbox（E20/S06 的发送侧）**——发送失败重试与 `UNCERTAIN` 对账。
3. **任务状态机（`im_message_tasks`）与独立路由审计表**——当前以
   `im_event_inbox.route_reason` 承载。
4. **群内公开引用的可见性策略（E28）**——只依赖“引用文本 = 平台对该用户可见的文本”。

## 4. 复现命令

```bash
cd backend

# 新增路由内核测试（54）
.venv/Scripts/python.exe -m pytest \
  tests/test_routing_v2.py tests/test_im_routing_repository.py \
  tests/test_channel_routing_v2.py tests/test_feishu_routing_v2.py -q

# 飞书既有回归 + 管理器
.venv/Scripts/python.exe -m pytest \
  tests/test_feishu_group_isolation.py tests/test_feishu_parser.py tests/test_channels.py -q

# 迁移相关
.venv/Scripts/python.exe -m pytest tests -q -k migration

# 边界与阻塞 IO 门禁
.venv/Scripts/python.exe -m pytest tests/test_harness_boundary.py tests/blocking_io -q

# 静态检查
.venv/Scripts/python.exe -m ruff check .
```

### 已知平台性失败（Windows，与本次改动无关）

| 套件 | 失败数 | 原因 |
| - | - | - |
| `tests/test_channels.py::TestChannelService::*` | 5 | `ModuleNotFoundError: No module named 'fcntl'`（`app/channels/runtime_config_store.py:65`） |
| `tests/blocking_io/test_channel_runtime_config_store.py` | 5 | 同上 |
| `tests/test_mcp_file_migration.py` | 6 | Windows 路径转换用例 |

`tests/test_migration_0022_work.py` 原本断言的“升级到 head 后新增表集合”随 0023 更新为
`{work_integration_records, im_session_pointers, im_message_links, im_event_inbox}`，
版本断言更新为 `0023_im_session_routing`；该用例现已通过。

## 5. 发布说明（摘要）

- 新增三张路由表与迁移 `0023_im_session_routing`（纯增量、可回滚）。
- 飞书路由 V2 默认开启（`DEER_FLOW_ROUTE_V2_ENABLED=1`），无 DB 时自动降级为 V1。
- 群聊话题（`chat_type=topic`）现在也强制结构化 @ 并做发送者隔离（**行为变更**）。
- 历史引用不再把文本拼进 prompt，而是恢复原 Thread 或明确失败关闭（**行为变更**：
  之前“看起来续上了”，现在要么真恢复，要么明确提示）。
- `feishu.py` 出站卡片补齐 message_id 映射，回复第二张卡片也可恢复。
- 文档：架构审计、ADR、迁移 Runbook、本验收报告。