# 迁移 Runbook：`0023_im_session_routing`

范围：新增 `im_session_pointers` / `im_message_links` / `im_event_inbox` 三张表，
以及飞书路由 V2 的灰度开关。迁移是**纯增量**：不修改、不重建、不删除任何既有表或行。

- 迁移文件：`backend/packages/harness/deerflow/persistence/migrations/versions/0023_im_session_routing.py`
- `revision = "0023_im_session_routing"`，`down_revision = "0022_work_integration_state"`
- 当前 head 唯一：`alembic heads` → `0023_im_session_routing`

## 1. 前置检查

```bash
# 1) 确认当前版本是 0022（若有其它 head 先停止发布）
alembic current

# 2) 确认 head 唯一，没有并发分支
alembic heads

# 3) 备份（PostgreSQL）
pg_dump --schema-only "$DATABASE_URL" > before_0023_schema.sql
```

多副本部署时先确认没有正在运行的 Gateway 升级循环；`bootstrap_schema()` 会在启动时自动
`upgrade head`，因此**首次启动新版本即完成迁移**，无需单独执行。

## 2. 执行

正常路径：滚动发布 Gateway 新版本，首个 Pod 启动时自动迁移。

手工执行（仅排障时）：

```bash
cd backend
PYTHONPATH=. uv run alembic -c packages/harness/deerflow/persistence/migrations/alembic.ini upgrade head
```

迁移内部用 `sa.inspect(conn).has_table(...)` 逐表守卫，重复执行安全（幂等）。

## 3. 验证

```sql
-- 三张表存在
SELECT table_name FROM information_schema.tables
 WHERE table_name IN ('im_session_pointers','im_message_links','im_event_inbox');

-- 关键唯一约束 / 索引存在
SELECT indexname FROM pg_indexes
 WHERE tablename IN ('im_session_pointers','im_message_links','im_event_inbox');

-- 版本已前移
SELECT version_num FROM alembic_version;   -- 期望 0023_im_session_routing
```

期望索引名：`uq_im_session_pointer_scope`、`uq_im_message_link_provider_message`、
`uq_im_event_inbox_key`、`idx_im_message_links_thread`，以及按 `provider` /
`connection_id` / `owner_user_id` / `thread_id` / `external_conversation_id` 的 `ix_*`。

功能验证（发布后）：

1. 群里 A 首次 @机器人 → `im_session_pointers` 出现 1 行（`channel_type='group'`,
   `generation=1`, `external_topic_id=''`），`im_message_links` 出现 inbound 记录。
2. 同一用户再 @一次 → 指针不变，仍 1 行。
3. 另一位同事 @机器人 → 新增 1 行（不同 `owner_user_id`）。
4. `/new` → 同一行 `generation` +1、`thread_id` 变化。
5. 回复自己的机器人回复（带 @）→ `im_event_inbox.route_reason='SELF_REPLY_RESTORE'`，
   指针 `generation` 不变。

## 4. 回滚

**代码回滚（推荐）**：把 `DEER_FLOW_ROUTE_V2_ENABLED=0` 后重启 Gateway。V2 方法全部短路，
回到 V1 的 JSON store 指针；三张表保留但不再写入，无副作用。

**结构回滚**（仅在确认不需要保留路由历史时）：

```bash
alembic downgrade 0022_work_integration_state
```

`downgrade()` 只 `drop_table` 这三张表（先索引后表），不触碰任何既有表。因此：
- 回滚会丢失 `im_message_links` 历史，之后无法再恢复“回复旧卡片”的场景；
- 回滚不会影响 Thread 本身与 Web 历史。

## 5. 灰度顺序

1. 数据库迁移（无开关，纯增量）。
2. `DEER_FLOW_ROUTE_V2_ENABLED=1`（默认值，可不显式设置），但只在一个测试群使用：
   目前开关是进程级而非按群，若需真灰度请部署单副本 Gateway 并把流量指到该副本。
3. 观察 24h：`im_event_inbox` 增长率与 `COMPLETED` 占比、`route_reason` 分布
   （`REPLY_MAPPING_MISSING` / `THREAD_DELETED` 长期非零说明适配器写 link 有缺口）。
4. 全量。

## 6. 监控与排障

| 现象 | 排查 |
| - | - |
| 用户说“@了没反应” | 适配器 @ 门禁：确认是**结构化 mention**（纯文本 `@` 不算），`bot_open_id` 是否解析成功（否则 `mention_only_in_group`/`bot_identity_unavailable` 审计） |
| “无法恢复原会话：没有找到对应的会话记录” | `im_message_links` 缺出站记录。检查发布前是否已禁用 V2（V1 卡片从未写入 SQL）——历史卡片天然无法恢复，属预期 |
| “原会话已被删除” | Thread 在 Gateway 侧不存在；确认是否被清理或跨环境 |
| 同一人被分裂成两个 Thread | 检查是否混用了 V1 JSON store（`DEER_FLOW_ROUTE_V2_ENABLED=0` 的实例与新实例并存） |
| `/new` 没有切换 | 查看 WARNING `"/new pointer switch contention"`（5 次 CAS 均失败，说明有并发写） |
| 重复回复 | `im_event_inbox` 唯一键未生效：确认迁移已到 0023、`event_key` 含 `message_id` |

## 7. 已知非问题

- Windows 开发机上 `tests/test_channels.py::TestChannelService::*` 与
  `tests/blocking_io/test_channel_runtime_config_store.py` 会因
  `ModuleNotFoundError: No module named 'fcntl'`（`app/channels/runtime_config_store.py:65`）失败，
  与本次迁移无关；Linux CI 正常。
- `tests/test_mcp_file_migration.py` 的 Windows 路径用例失败同为平台问题。