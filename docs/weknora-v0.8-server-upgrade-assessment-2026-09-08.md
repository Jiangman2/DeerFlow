# WeKnora v0.8.0 服务端企业版升级评估

评估日期：2026-09-08

当前企业版：WeKnora v0.7.0，`deerflow.local/weknora-app:0.7.0-enterprise-proxy1-arm64-20260723`

目标上游版本：WeKnora v0.8.0，commit `1edcd54b43606d9079bb36650efe3f68707a79ea`

## 1. 执行结论

可以升级，并且可以保留 DeerFlow 对 WeKnora 的企业化改造，但不能直接把现有 App 镜像替换为官方 v0.8.0 镜像。

推荐采用“服务端先行”的两阶段升级：

1. 第一阶段只升级 WeKnora App 和数据库，继续使用现有 v0.7 前端、DocReader、MinerU 适配器、Gateway、Nginx 和企业配置。
2. 第一阶段构建 v0.8.0 企业 App 时关闭 AnyDoc 和 Sandbox，重放 `enterprise-proxy1` 补丁，保持当前文档解析与网络代理行为。
3. 第二阶段再单独迁移 v0.8 前端、DocReader 和新能力；不能把 v0.7 的哈希前端文件挂载到 v0.8 UI 镜像。

当前生产切换判定为 **No-Go**，但准备和影子演练可以立即开始。阻塞生产切换的条件有：

- 当前 Codex 环境不能直连生产 `datax-01`；后续继续采用离线包、服务器执行日志和校验文件闭环。
- v0.8.0 包含 20 个数据库迁移，其中有删除和全表数据改写；尚未在生产数据副本上完成迁移演练。
- v0.8.0 ARM64 企业镜像已完成可复现构建和三节点离线导入，但尚未完成影子环境运行冒烟与 v0.7 UI 兼容验证。

只要以上门禁在维护窗口前全部通过，当天可以进行服务端切换。任何一项未通过，都不应对生产数据库执行迁移。

### 2026-09-08—09 生产预检进展

- 已校验企业源码归档和旧 v0.7 OCI 的 SHA-256；企业源码基线是 `e8dcbb07d09db88894601b564ebabe21dda6830d`。
- 已证明 App 内唯一有业务意义的源码改造是 SSRF-safe HTTP transport 增加 `Proxy: http.ProxyFromEnvironment`，并配有聚焦单测；不是 IAM、数据库或业务逻辑分叉。
- 已把该补丁移植到 v0.8 的共享 `NewSSRFSafeTransport`，固定上游源码、Go 构建镜像和官方 v0.8 ARM64 运行时摘要，并明确不链接 AnyDoc。
- 生产数据库为 PostgreSQL 16.14、约 24 GB，迁移版本为 `70|false`；Redis `PONG`、AOF 正常，WeKnora App 当前健康。
- ARM64 原生构建已在 datax-01 通过：企业代理聚焦单测、`make build-prod` 和 OCI 导出校验均成功；镜像 manifest 为 `sha256:9da770831e13ca124bd6cb138b41e8313e97ae1c8107ee16f9c47195bf546d55`。
- 同一份 OCI 已分别导入 datax-01、datax-02、datax-03，三台节点均报告 `PASS`，manifest 一致，且每次导入均确认 `production_deployment_mutated=no`、`database_mutated=no`。三节点镜像预热门禁已完成。
- 已生成并离线校验只读数据安全预检包，用于补齐 PVC/快照能力、挂载容量、PostgreSQL 备份工具与锁/WAL 状态、迁移风险表以及 Asynq 各状态队列计数；执行该包不会部署 v0.8 或执行数据库迁移。
- 数据安全预检首次执行在三节点清单解析阶段停止：Kubernetes 某条节点镜像记录的可选 `names` 字段为 `null`。停止点早于 PostgreSQL/Redis 检查，未改变生产；r2 已兼容 `null`/空数组并通过对应夹具、包内校验和及 Bash/Python 语法验证。
- 2026-09-09 10:23 的 r2 数据安全预检已完整 `PASS`，并再次确认 `production_deployment_mutated=no`、`database_mutated=no`。PostgreSQL 仍为 `70|false`，无等待锁、无超过 30 秒的事务、无活动 vacuum，备份/恢复工具均为 16.14。
- Kubernetes 未安装 VolumeSnapshot API/VolumeSnapshotClass；WeKnora PostgreSQL、文件、Redis、Neo4j 均为本地 RWO PV。因此不能把 CSI 快照作为回滚基础，必须使用 PostgreSQL 一致性备份和 datax-03 文件数据冷备/校验的组合方案。
- Asynq 当前有 pending 135、active 19、scheduled 2、retry 1，另有与 active 对应的 lease 19，`write_freeze_gate=NOT_READY`。当前生产仍在处理任务，不能开始一致性备份或迁移。
- 已收到并验证 r2 证据归档：外层 SHA-256 为 `94008a50a163af9b349e15e8bcae117a9a431ed4b5b09bfd780df56973daaf65`，内部 `EVIDENCE.SHA256` 的 14 项全部匹配。
- 已生成在线影子迁移演练包。它只通过 `pg_dump` 读取生产库，把 dump、独立 shadow PGDATA 和迁移验收结果写入 datax-02 的备份 PVC；所有恢复、70→90 迁移和校验只连接独立 shadow Service，不需要等待当前 Asynq 排空，也不改变生产工作负载或 schema。
- 首轮预检包的 `EVIDENCE.SHA256` 只有主日志自身不匹配：脚本在日志仍写入时计算了它的摘要；其余附件均匹配。后续包不再把正在写入的主日志放入内部清单。

## 2. 已核实的版本差异

上游正式版本从 [v0.7.0](https://github.com/Tencent/WeKnora/releases/tag/v0.7.0) 升到 [v0.8.0](https://github.com/Tencent/WeKnora/releases/tag/v0.8.0)。完整上游差异见 [v0.7.0...v0.8.0](https://github.com/Tencent/WeKnora/compare/v0.7.0...v0.8.0)。

本地对固定 commit 的审计结果：

- 变更 1,844 个文件。
- 新增 306,651 行，删除 52,424 行。
- 数据库版本从 70 升到 90，共 20 个新迁移。
- v0.8 新增或强化 Sandbox、Skills、长期记忆、AnyDoc、数据源、Agent 工具、Artifacts、上下文压缩等能力。

变化量说明旧企业镜像中的编译产物或源文件不能直接覆盖到 v0.8；必须把企业改造整理成最小补丁，在固定的 v0.8.0 源码上重新应用和测试。

## 3. 企业改造保留边界

| 改造层 | 当前所有者 | 升级风险 | v0.8 处理方式 |
| --- | --- | --- | --- |
| DeerFlow SSO、身份映射、租户归属、管理员只读访问 | Gateway | 低 | 原样保留，跑 v0.8 契约测试 |
| 知识库、文件、标签范围控制与 10 个 WeKnora 工具 | Gateway/Harness | 低到中 | 原样保留，验证响应和 SSE 兼容性 |
| `/platform/` 同源代理与路由顺序 | Nginx | 低 | 原样保留 |
| 内部模型 relay、CA、SSRF 白名单、`NO_PROXY` | K8s/Gateway | 中 | 保留配置；重测 v0.8 HTTP transport |
| `enterprise-proxy1` App 网络代理补丁 | 自定义 WeKnora App | 高 | 从生产源码提取精确补丁并移植到 v0.8 |
| MinerU 默认路由与扫描 PDF 策略 | SQL/外部适配器 | 中 | 第一阶段保持现状，防止 AnyDoc 抢占路由 |
| DocReader 的 XLSX/Web 兼容补丁 | ConfigMap/DocReader | 中 | 第一阶段保留 v0.7 DocReader；第二阶段再升级 |
| 普通用户简化导航、管理员完整导航、隐藏退出等 UI 策略 | v0.7 哈希前端补丁 | 高 | 第一阶段保留旧 UI；第二阶段改为 v0.8 源码级实现 |
| WeKnora 数据库 | PostgreSQL | 最高 | 影子演练、显式迁移 Job、PITR/快照回滚 |

现有企业能力大部分位于 DeerFlow Gateway、Nginx、Kubernetes overlay 和外部解析适配器中，不会因为更换 WeKnora App 源码而自动丢失。真正必须移植的上游内改主要是 App 的网络代理补丁，以及后续 v0.8 UI 的企业策略实现。

## 4. 服务端 API 兼容性

v0.8.0 仍保留 DeerFlow 当前依赖的主要路径：

- `GET /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases/{id}`
- `GET /api/v1/knowledge-bases/{id}/knowledge`
- `GET /api/v1/knowledge/{id}`
- `GET /api/v1/chunks/{knowledge_id}`
- `POST /api/v1/knowledge-search`
- `POST /api/v1/sessions`
- `POST /api/v1/knowledge-chat/{session_id}`
- `GET /api/v1/agents`
- `POST /api/v1/agent-chat/{session_id}`
- 共享知识库读取路径

已看到的协议变化以新增字段和新增 SSE 事件为主。当前 DeerFlow 解析器会忽略未知事件，因此对既有问答、检索和引用链路没有静态阻断。v0.8 的 `complete` 事件可包含 artifacts/usage，DeerFlow 当前不会消费这些新字段；这不影响现有功能，但意味着第一阶段不会自动获得完整的新 Artifacts/Skills 体验。

结论：静态接口审计为兼容，但必须用影子 v0.8 服务跑真实契约测试，不能只靠路由名称判定生产兼容。

## 5. 数据库迁移风险

v0.8.0 新增迁移 `000071` 到 `000090`：

| 迁移 | 主要变化 | 风险 |
| --- | --- | --- |
| 071 | API key scope、约束和索引 | 中 |
| 072 | 多表时间列转换为 `timestamptz` | 高：可能锁表和重写 |
| 073 | audit log 字段与复合索引 | 中到高 |
| 074 | MCP OAuth lease 字段 | 低 |
| 075 | Wiki revision 表和索引 | 中 |
| 076 | knowledge metadata 表达式部分索引 | 中到高 |
| 077 | 删除 `wiki_log_entries`，删除 `page_type='log'` 的页面 | **高：不可逆数据删除** |
| 078 | chunk revision 字段/表；回填 `source_content=content` | **高：大表写入、WAL 和磁盘压力** |
| 079 | 新增 `folder_path`，把含 `/` 的 `file_name` 拆分并改写 | **高：数据语义变化** |
| 080 | 知识库自动标签配置 | 低 |
| 081 | message artifacts | 中 |
| 082-083 | Sandbox 配置与 session 关联 | 低到中 |
| 084 | 长期记忆表、字段和索引 | 中 |
| 085 | message usage | 低 |
| 086-090 | Tenant Skills、版本、环境变量、catalog 和数据整理 | 中 |

额外注意：历史迁移 `000044_audit_log.up.sql` 在两个 tag 间被修改。对已经处于版本 70 的数据库，它不会重新执行；但新建 v0.8 数据库与历史升级数据库可能存在谱系差异，因此 CI 中必须同时验证“从零建库”和“70→90 升级”两条路径。

v0.8 默认 `AUTO_MIGRATE=true`、`AUTO_RECOVER_DIRTY=true`，而迁移失败只记录警告，不阻止 App 启动。企业部署必须改为：

- App：`AUTO_MIGRATE=false`
- App：`AUTO_RECOVER_DIRTY=false`
- 使用与 App 完全相同的企业镜像运行一次性 migration Job。
- migration Job 成功后检查 `schema_migrations = 90, dirty = false`。
- App 增加启动/就绪门禁；数据库不是 90 或处于 dirty 时拒绝对外服务。
- 不直接使用会输出完整数据库 URL 的上游 `scripts/migrate.sh`；使用不打印 Secret 的包装命令。

迁移 077、078、079 决定了回滚不能只把 Deployment 改回 v0.7。生产回滚必须恢复迁移前的 PostgreSQL 快照/PITR，同时恢复与数据库一致的文件存储快照。

## 6. 文档解析和 AnyDoc

v0.8 App 镜像默认以 `WITH_ANYDOC=1` 构建。AnyDoc 会成为多个 Office/PDF 格式的默认解析器；当前企业部署则把 PDF、DOC/DOCX、PPT/PPTX 显式路由到 MinerU，并对 XLSX/Web 使用定制 DocReader。

显式 `parser_engine_rules` 仍优先于 v0.8 默认选择，因此现有 MinerU 路由可以保留。但当前没有对所有 Excel 格式显式指定引擎，启用 AnyDoc 会改变 XLS/XLSX/XLSM/ODS 的实际解析路径，绕过现有 DocReader 兼容层。

第一阶段建议：

- 企业 App 以 `WITH_ANYDOC=0` 构建。
- 继续使用当前 DocReader 镜像和兼容补丁。
- 明确保留 PDF、DOC/DOCX、PPT/PPTX → MinerU 的规则。
- Sandbox 显式关闭，不能给 App 挂载 `docker.sock`。

v0.8 App 对旧 DocReader 的 `ReadStream` `Unimplemented` 有回退到 unary `Read` 的兼容逻辑，且原有 proto 没有破坏性变化。因此第一阶段保留当前 DocReader 在协议上可行，但仍要跑大文件、图片和引擎发现测试。

第二阶段再单独评估 AnyDoc 与 v0.8 DocReader，逐格式做金样对比后启用。

## 7. 企业代理补丁

生产源码与固定 v0.7 上游的审计已经完成。排除预检时主动省略的 `.env.*`、Windows 对中文文档名的显示差异和构建文件后，唯一有业务意义的 Go 改动为：

```go
return &http.Client{
    Transport: &http.Transport{
        Proxy: http.ProxyFromEnvironment,
        // 原有 SSRF-safe DialContext 保持不变
    },
}
```

v0.8 将 transport 构造重构为共享的 `NewSSRFSafeTransport`，因此补丁移植为在该函数的 `http.Transport` 上设置同一个标准库代理解析器，同时保留 `SSRFValidatingRoundTripper`、`SSRFSafeDialContext`、重定向复检、系统代理识别和白名单逻辑。聚焦测试断言 `transport.Proxy != nil`。

这证明企业代理改造可以完整保留，而且补丁没有放宽初始 URL、重定向目标或最终拨号的 SSRF 校验。仍需在影子 v0.8 服务中对 chat、embedding、rerank、VLM、ASR 分别验证代理/直连、`NO_PROXY`、企业 CA、证书链和主机名校验；代理 Secret 只在构建时作为参数读取，不写入源码归档或镜像层。

## 8. v0.7 前端保留策略

服务端第一阶段继续运行当前 v0.7 UI，因此用户界面和以下企业策略不变：

- DeerFlow SSO 自动引导。
- 普通用户只显示 New Chat、Knowledge Bases、Shared Spaces、Agents。
- 管理员保留完整导航。
- 普通用户隐藏全局 Publish Integrations、系统设置和嵌入页退出。
- 新建知识库只展示 Basic Information。
- 已有知识库保留 Basic Information 与 Sharing Management。

这条路径必须通过 v0.7 UI → v0.8 App 的实际 E2E 验证后才能上线。

第二阶段升级 UI 时，不能复用以下 v0.7 构建产物：

- `k8s/weknora/ui-patches/index-itUW58et.js`
- 固定引用 v0.7 哈希 chunks 的 `k8s/weknora/ui-patches/index.html`

企业策略应迁移到 v0.8 前端源码中的 auth、menu store、knowledge-base 表单和用户菜单组件，再构建固定 commit 的企业 UI 镜像。Kustomize 只负责配置和镜像 pin，不再覆盖整份哈希 bundle。

## 9. 可复现构建方案

建议建立以下版本管理边界：

- 固定上游：版本、commit、源码归档 SHA-256。
- 企业 patch series：每个补丁单一目的，记录来源和测试。
- 企业 App Dockerfile：固定 Go/Rust/迁移工具版本；第一阶段 `WITH_ANYDOC=0`。
- 镜像：生成 linux/arm64 OCI archive、digest、SBOM 和签名/校验文件。
- CI：验证补丁可应用、工作树无意外差异、Go 测试、API 契约、迁移 lint 和镜像架构。

上游 Dockerfile 安装 `migrate@latest`，企业构建不应继承浮动版本；建议固定为已验证的 `golang-migrate` 版本并记录 SHA/digest。

第一阶段镜像建议使用新标签，例如：

```text
deerflow.local/weknora-app:0.8.0-enterprise-proxy1-arm64-20260908
```

标签只是可读标识，Deployment 和迁移 Job 最终都应固定同一个镜像 digest。

## 10. 当天执行门禁

### A. 生产只读预检

- 当前镜像、Deployment env、ConfigMap、Secret 名称和 Kustomize 渲染结果归档。
- `schema_migrations` 必须为 `70|false`。
- 收集迁移涉及表的行数、大小、索引大小和锁状态。
- 统计 `wiki_log_entries`、`wiki_pages.page_type='log'`、`chunks.source_content=''`、含 `/` 的 `knowledges.file_name` 数量。
- 确认 Asynq 待处理/运行任务为零，停止新文档写入。
- 记录数据库、Redis、Neo4j 和文件 PVC 容量；迁移前应为 `chunks` 大表回填保留足够 WAL 和临时空间。
- 导出企业源码和现有 App OCI 镜像，校验 SHA-256。

已取得的关键生产量化结果：

- `embeddings`：2,955,814 行，约 20 GB。
- `chunks`：1,740,502 行，总占用约 2.94 GB；迁移 078 会大规模回填 `source_content`。
- `wiki_log_entries`：8,346 行；迁移 077 会删除整表，必须先独立归档并验证可恢复。
- `wiki_pages.page_type='log'`：0 行。
- 文件名含 `/` 的 `knowledges`：13,896 行；迁移 079 会改变这些记录的 `file_name/folder_path` 语义，必须导出前后映射并抽样核对真实文件。
- Redis 共 3,762 个键，其中约 3,761 个为 Asynq 相关键；这不能证明队列已排空，生产冻结前仍需读取各队列的 pending/active/scheduled/retry/archived 数量。
- PostgreSQL、文件、Redis、Neo4j 使用跨 datax-02/datax-03 的本地/RWO PVC；回滚必须协调数据库与文件数据，不能依赖 Deployment rollout history。

2026-09-09 10:23 的增量实测结果：

- `wiki_log_entries` 已增至 8,362 行，文件名含 `/` 的 `knowledges` 已增至 14,013 行，证明预检期间生产数据仍在变化；最终删除/改写清单必须在正式停写后重新生成。
- PostgreSQL 数据库约 24 GB；底层挂载可用约 2.44 TiB，`pg_wal` 约 464 MiB。文件、Redis、Neo4j 所在底层挂载可用约 2.78 TiB，容量不是当前阻塞项。
- PostgreSQL `archive_mode=off`、无复制 slot，不能依靠 WAL 归档或现有副本进行时间点回滚；`pg_dump`、`pg_restore`、`pg_basebackup` 16.14 均可用。
- Asynq 未完成任务至少包括 default 队列 pending 65/active 6、multimodal pending 70/active 8、question active 3、summary active 1/retry 1、wiki active 1/scheduled 2。lease 与 active 重叠，不作为独立任务重复计数，但任何非零状态都足以阻止停写门禁。

### B. 备份与恢复证明

- 当前没有 CSI VolumeSnapshot、WAL 归档或可用副本，不能把快照/PITR 当作既有能力。
- 影子演练先生成事务一致的 PostgreSQL 逻辑 dump，并在独立 PostgreSQL 实例上实际恢复。
- 正式停写后同时生成最终逻辑 dump 与 `pg_basebackup -X stream` 物理备份；记录 PostgreSQL 版本、起止 LSN、耗时、大小和 SHA-256。
- App 停止后，对 datax-03 的 WeKnora 文件目录创建一致性冷备和清单；Neo4j/Redis 分别归档，Redis 不作为知识数据的唯一恢复来源。
- 在不触碰生产库的前提下完成至少一次恢复验证，并测量恢复时长是否满足维护窗口。

只有“创建了备份”不够，必须有一次成功恢复证明。

### C. 影子迁移

- 从生产副本恢复独立 PostgreSQL 和文件存储。
- 运行固定 digest 的显式 migration Job。
- 记录每个迁移耗时、锁等待、WAL 增量和磁盘峰值。
- 对迁移前后租户、用户、知识库、知识、chunks、agents、sessions、messages 做数量对账。
- 对 077 删除对象和 079 文件名改写单独生成清单并由业务确认。
- 检查 `schema_migrations = 90|false`，再启动影子 v0.8 App。

当前演练包额外执行以下硬门禁：生产库必须保持 `70|false`；只允许 dump Job 连接生产 `weknora-postgres`；shadow 资源全部固定到 datax-02 且使用独立 Secret/Service；077 必须删除 `wiki_log_entries`，078 必须完成全部 chunks 的 `source_content=content` 回填，079 必须逐行匹配迁移前导出的预期 `folder_path/file_name` 映射，072 的 8 个授权时间列必须转换为 `timestamptz`。影子资源和备份在审阅完成前不会自动删除。

### D. 服务端验收

- SSO：普通用户、系统管理员、身份切换、首次个人工作区创建。
- RBAC：跨租户拒绝、共享空间只读/可编辑、管理员只读视图。
- DeerFlow 10 个 WeKnora 工具：知识库/文件/标签选择、搜索、问答、Agent、引用和图片。
- v0.7 UI：登录、导航、知识库 CRUD、共享管理、聊天和文件预览。
- 文档：搜索型/扫描型 PDF、DOC/DOCX、PPT/PPTX、XLS/XLSX、网页和图片。
- 模型：chat、embedding、rerank、VLM、ASR；验证企业 CA、代理和 SSRF 拒绝。
- 压力：并发检索、上传、解析队列，确认现有 MinerU 六 worker 上限不被破坏。

### E. 生产切换

1. 宣告维护，冻结 WeKnora 写入并排空任务队列。
2. 记录最终对象计数，创建最终数据库和存储快照。
3. 将 WeKnora App 缩容到 0；保持 Gateway/Nginx 的维护响应。
4. 运行显式迁移 Job；失败立即停止，不启动 App。
5. 检查 `90|false` 和迁移后数据对账。
6. 部署固定 digest 的 v0.8 企业 App，仍保持 `AUTO_MIGRATE=false`、`AUTO_RECOVER_DIRTY=false`、AnyDoc/Sandbox 关闭。
7. 完成内部冒烟后开放流量，持续观察错误率、队列、数据库锁/WAL 和解析成功率。

### F. 回滚触发条件

- schema dirty、迁移/对账不一致。
- SSO、租户隔离或共享权限失败。
- 任一内部模型代理/CA 失败。
- 核心解析类型与当前生产金样不一致。
- v0.7 UI 的知识库/聊天关键路径失败。
- 错误率、队列积压或数据库资源超过预定阈值。

迁移开始前可以直接回到旧 Deployment。迁移开始后必须恢复数据库和文件快照，再恢复 v0.7 App；不能只回滚镜像。

## 11. Go/No-Go 清单

生产升级只有在以下项目全部为 Go 时才能执行：

- [x] 已导出并校验 v0.7 企业源码与旧 OCI 镜像。
- [x] 已生成、审阅并完成 v0.8 企业补丁聚焦单测与 ARM64 构建验证。
- [x] v0.8 linux/arm64 企业镜像已固定 manifest，并已导入三个节点。
- [ ] 影子 70→90 迁移成功，耗时和空间满足维护窗口。
- [ ] 077 删除清单、079 文件名改写清单已确认。
- [ ] 数据库与文件备份完成，且恢复演练成功。
- [ ] v0.7 UI → v0.8 App E2E 通过。
- [ ] SSO、RBAC、10 个工具、解析、模型代理和引用链路全部通过。
- [ ] migration Job、App schema gate、自动/人工回滚脚本已演练。
- [ ] 维护窗口和业务写入冻结已确认。

## 12. 最终建议

推荐批准“v0.8.0 服务端第一阶段升级方案”，但不批准未演练的直接生产替换。

企业源码、`enterprise-proxy1` 最小补丁、ARM64 原生构建和三节点镜像导入已经完成。今天下一步应先执行只读数据安全预检，补齐 PVC/快照与容量、Asynq 队列状态和 PostgreSQL 备份条件，再做数据库与文件备份恢复证明以及影子 70→90 迁移。若影子迁移、恢复证明或企业补丁运行验证赶不上维护窗口，应把今天限定为构建与演练，不对生产数据库执行 071-090。
