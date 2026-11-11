# Work 语义分流与任务退出

2026-11-04 已在独立候选中对齐最新0716 `ef4ce7a1`；保留本文件原基线与历史验收说明。
最新升级边界、出口/代理及70 Pod维护安排见 [上线前核验](WORK_RELEASE_PREFLIGHT_20261104.md)。

后续日历任务草稿、领域工具循环与有限批量的候选说明见 [WORK_CALENDAR_TASK_AGENT.md](WORK_CALENDAR_TASK_AGENT.md)。下文为同日首版分流及共享任务屏障说明；旧版验收记录单独保留。

本候选基于 `codex/enterprise-consolidation-20260716` 的 `0dc61aa7`。
代码和隔离测试结果不是生产部署或真实 Work 操作验收。

## 行为边界

- 每条新的聊天消息以当前认证入口为准，不继承线程的历史 Feishu 标签。
- 只有已绑定 Work 私聊经过语义规划；网页、其他 IM、OA 和定时任务不挂载 Work 规划器。
- 网页打开原 Work 对话再输入仍是网页消息，不因此获得 Work 工具或 OAuth 身份。
- OAuth 浏览器回调续接原任务，仍验证一次性 state、浏览器 cookie、原发送者和当前绑定；不是新的网页聊天。
- 上传附件保留普通平台处理路径。附件和 Work 文档链接混用时先提示拆开发送，不把附件吞入空的 Work 任务。

自然语言不再经过“查询/查看”等操作词白名单。`plan_turn` 一次生成
`WorkTurnDecision`，区分普通任务、新任务、继续任务、补问、取消当前任务和未开放操作，
并提取既有 `CalendarIntent` 或文档提议。OAuth 返回后复用已验证的意图快照，不重新猜测用户要求。
普通 Work 聊天会额外经过一次有时限的语义判断；其他入口不增加这次调用。

日期缺失、新建缺时长、编辑缺变更内容时，在个人 OAuth 前补问。下周、第二条、半小时等短答
结合本人当前任务理解。常见整日范围由语义 `time_window` 枚举选择、服务端计算；授权返回后不按新时钟重算。
模型只能提出意图，不能选择 owner、token、API 地址或跳过本人确认。模型超时/无效结构
返回明确的重试引导，不转普通模型编造“没有接入日历”。

## 来源和普通回答

Gateway 每轮重写 runtime-only `message_source`，内部已认证渠道可带 provider；浏览器和无渠道内部任务
分别为 web/internal。自由配置中的来源和能力提示会被移除，历史线程 metadata 仅用于展示/审计。
现有 GitHub 等渠道仍使用经过认证的 channel_name 策略，来源不是绑定或资源访问授权。

当前 Work 普通回答收到服务端固定能力指导：平台支持个人日历和云文档，实际可用性由后端给出；
不能编造授权链接、数据或执行回执。该提示不向网页和其他渠道注入，也不添加执行工具。

## 无表结构变更的任务屏障

继续使用 `work_integration_records` 的既有字段和 AES-GCM 加密 JSON：

- 新增 `conversation` 等记录类型，而不是新建表/列；不新增 Alembic revision，不改 ORM 表定义。
- 任务身份为 `task_id + generation`；generation 随任务切换或继续生成随机新值，不复用过期标记。
- 每行 `revision` 仍用于 CAS，与任务 generation 不混用。
- 新用户消息先预留 turn_id，模型只对该时刻的任务/候选快照规划；迟到规划不得覆盖新消息。
- 事务内按固定顺序锁 admission guard、conversation、job/plan/state。核对当前任务与认领执行在同一事务中完成；
  不在锁内等待模型、Work HTTP 或用户。
- 70 Pod 共用 PostgreSQL，不靠单 Pod 内存决定旧任务是否有效。memory profile 仅用于明确的单进程本地 UAT。

换话题会结束补问、取消未执行 job/OAuth state/preview，并使旧确认码失效；保留有效 grant、渠道绑定和历史回执。
迟到读结果不能恢复旧上下文。已经认领的写入保留 executing/completed/uncertain 记录，照实报告结果，禁止盲目重试。
取消当前请求与取消已创建日程是两种操作；后者仍未开放。

## 配置和上线顺序

原有 app、Secret、profile、回调、状态密钥和权限配置保留。增加可选
`DEER_FLOW_WORK_TURN_TIMEOUT`，默认90秒，范围5～180秒，约束分类及领域理解的总执行时间；显式旧值30继续有效。
`DEER_FLOW_WORK_TURN_QUEUE_TIMEOUT` 独立限制等待模型执行槽位的时间，默认10秒，范围1～30秒，排队不占模型执行预算。
领域理解最多6步、最多2次格式修复，完整提议通过本地验证后直接返回；服务另设1秒防御watchdog，不增加重试。
不要求 ITC 为本轮增加权限，也不扩大原有写入白名单。

1. 先在隔离环境验证，保留当前旧版 UAT 作对照。旧本地 `0020_work_integration_state` 库不能直接交给最新
   `0020_ppt_confirmation -> 0021_oa_result_sets -> 0022_work_integration_state` 链启动；使用独立测试库。
2. 运维只读核对生产已有 Work 表、0022 和状态密钥一致。本轮无需新增迁移或手工修改生产业务数据。
3. 生产更新前暂停 Work 新业务接入并等待已确认写入完成。保留绑定、有效 grant 和 executing/uncertain 回执。
4. 所有70个 Gateway 更新至兼容新协议的相同版本后，再恢复 Work 接入。不能在旧 Pod 仍处理任务时启用新协议：
   旧代码不检查 generation，混跑不能提供完整取消屏障。
5. 升级前未完成的旧授权链接/预览在新协议中拒绝续跑，用户重新发送请求；已有效授权仍按主体和 scope 复用。
6. 恢复后先做小范围业务验收，再开放能力。现有owner名单在普通消息的模型规划之后校验，不是流量隔离开关；`DEER_FLOW_WORK_ENABLED=false`也不是安全维护暂停。回退先暂停新任务并结束新预览，保留状态表、密钥和执行回执；不能依赖旧代码读取新任务来安全回退。

## 验证边界

自动回归覆盖来源伪造、网页续聊旧线程、普通任务和附件、语义提议校验、补问、迟到规划/回调、
跨主体 token、确认去重、切题与写入竞争、SQLite 和独立 PostgreSQL。模型模拟测试不证明语义准确率；
另以脱敏人工语句实测实际配置模型。独立进程共享 PostgreSQL 验证跨进程屏障，不宣称已完成70 Pod生产容量验收。
真实 Work 授权/日历/文档仍需使用 UAT 测试账号验收，且依赖应用权限、网络和出口白名单。

本轮验证记录和人工复测清单见 [WORK_ROUTING_VALIDATION_20261102.md](WORK_ROUTING_VALIDATION_20261102.md)。
