# 上游升级与金仓验收

本次范围为 DeerFlow 主库的金仓 V9 适配，以及企业分支持续同步上游。达梦和 WeKnora 数据库迁移不在本次范围。

## 基线

`baseline.json` 区分本地观察到的上游提交与已经验收的发布。merge-base 只表示历史共同祖先，不表示该点之后所有上游功能都已缺失，也不表示企业版本已认证。认证字段在真实验收前保持 null。

远程统一为 `origin`（企业 fork）和 `upstream`（官方只读）。企业稳定集成分支为 `codex/enterprise-consolidation-20260716`。

## 同步流程

1. 获取官方更新，选择稳定标签并记录其解析后的完整提交 SHA；没有合适标签时，明确记录经审查的固定 SHA。
2. 从企业集成分支创建 `codex/sync-upstream-<版本>`，在独立工作区执行普通 merge。
3. 逐项审查共享接入点，不批量使用 ours/theirs。保留共享分支历史，不 rebase 已发布企业提交。
4. 比较配置、数据库迁移、依赖锁文件、检查点和 Store API 变化；更新核心补丁台账。
5. 运行官方回归、企业权限及任务回归、PostgreSQL 回归和真实金仓验收。
6. 验收通过后合入企业分支，创建企业发布标签，并更新基线及证据位置。

紧急安全修复可单独 cherry-pick，必须记录原提交及后续正式同步时的去重结论。

## 金仓技术边界

默认 PostgreSQL 路径保持不变。通过独立兼容配置验证目标服务器及协议能力，优先复用 SQLAlchemy/asyncpg、Psycopg 3、LangGraph Checkpointer 和 Store。不得在未验证时宣称直接兼容，也不得自动退回内存或更换成私有检查点实现。

PoC 必须先于生产迁移：确认厂商精确构建、PG 模式、UTF-8、目标架构、授权及客户端组合。用隔离数据库验证 JSONB、BYTEA、时间戳、参数绑定、部分索引、ON CONFLICT RETURNING、会话锁与事务锁。

运行实际应用建库迁移、Checkpointer/Store setup 和读写；测试历史检查点恢复、并发事件顺序、断连重连及全部写入入口。只验证 SQL 或 mock 不能替代这些验收。

## 发布证据

每次候选发布记录 Python、SQLAlchemy、asyncpg、Psycopg、连接池、LangGraph、数据库补丁、迁移工具和原生 CPU 架构版本。数据库敏感依赖变化后重新验收。

必须完成存量数据迁移演练（含 Alembic 与 LangGraph 迁移元数据）、结构和数据一致性检查、序列检查、历史会话恢复、备份恢复。所有写入者须在割接期间停写，包括 Gateway、PPT Runner、定时任务和 IM 入口。

数据库使用独立 PVC。数据库镜像、许可证和凭据不进入 Git。目标数据库开放生产写入后，没有反向同步就不能承诺无损切回旧库。代码回退不代表数据结构可以回退。

## 实施状态

已建立基线、升级流程、差异清单、数据库代码漂移 CI 和自动化 PoC。已通过本地目标保护、图恢复场景、Git 差异分类、漂移检测与代码检查；CI 尚未在远程执行。真实金仓 PoC 未运行，已新增通用维护数据库接入与双架构部署候选；更多兼容差异、存量迁移及恢复验收仍待真实环境。现阶段未认证，不得用于生产发布声明。

## 自动生成差异清单

运行 `python scripts/upstream/inventory.py --upstream upstream/main --enterprise HEAD --output docs/upstream/inventory.json`。工具只读 Git，不拉取网络，也不修改分支。清单记录完整 SHA，并以共同祖先到企业提交的变更为口径；官方现存或祖先曾存在的文件均逐项列入共享审查范围，删除文件也会保留。企业新增文件只记录总数，避免第三方 Skill 和图标资源让清单膨胀。

清单不包含未提交改动，也不自动宣称补丁已审查。每项状态初始为 pending；审查人需要在 patch-ledger.json 记录具体修改目的、对应测试及删除条件。第三方 Skill 新增资源不应与共享核心冲突量混为一谈。

## CI 漂移检查

企业分支的 `enterprise-database-guards.yml` 检查 persistence-watch.json 中记录的候选指纹，覆盖持久化实现、迁移、检查点、Store、数据库配置、依赖锁文件和验收脚本。新增或删除迁移也触发失败。Windows/Linux 换行差异被归一化。

运行 `python scripts/upstream/check_persistence_drift.py`。失败后先审查列出的变更，再用 `--record-candidate` 记录新候选指纹，并重新执行适用的真实数据库验收。更新指纹不代表通过认证；状态始终是 candidate_not_certified。CI 此阶段仅执行不依赖专有数据库的保护及场景测试。
