# 金仓真实环境初步验证

使用后端锁定环境及 postgres 依赖运行 `probe.py`。由运维先创建空的专用测试库，库名必须为 `deerflow_kingbase_poc_` 加小写字母、数字或下划线。通过安全的环境变量注入 `KINGBASE_TEST_DSN`（标准 postgresql URL）；不要把密码放在命令参数、报告或仓库中。

从仓库根目录运行，例如：

```powershell
backend/.venv/Scripts/python.exe scripts/kingbase/probe.py --confirm-scratch-database deerflow_kingbase_poc_ci --report .deer-flow/kingbase-poc.json
```

工具会执行建表、检查点和 Store setup，留下测试表及检查点。只能针对隔离空库执行，结束后由运维按其流程回收整个测试库。不会访问 WeKnora，也不会切换应用连接。

初步覆盖：asyncpg 类型往返、JSONB、部分索引、upsert、两连接锁互斥及释放、Psycopg 3 pipeline、LangGraph 检查点与 pending writes 重连读取、Store 重连及查询删除。

报告保留失败阶段、异常类型、规范 SQLSTATE 及工具自有检查的诊断，不打印底层异常，避免泄露凭据或 SQL 数据。失败返回非零退出码；不得通过显示截断管道掩盖退出码。整个探测有 240 秒上限。

即使所有探测通过，production_certified 仍为 false：厂商 PG 模式证明、存量 SQLAlchemy 应用升级、实际历史会话恢复、网关流程、目标架构、数据迁移和备份恢复仍需完成。具体数据库精确构建和 PG 模式须由厂商/运维提供证据；banner 匹配不能替代这些证据。

## 已确定的远端目标

目标为 V9R1C10，分别在 x86_64 和 ARM64 原生环境运行。当前电脑没有金仓测试实例，禁止把本地静态测试标记为数据库验证通过。

远端 Linux 服务器使用与候选应用相同的依赖锁定环境（包含 postgres extra），从仓库根目录执行：

```sh
backend/.venv/bin/python scripts/kingbase/probe.py --confirm-scratch-database deerflow_kingbase_poc_ci --report .deer-flow/kingbase-v9r1c10-poc.json
```

连接仍通过 KINGBASE_TEST_DSN 环境变量注入，不写进命令。x86_64 与 ARM64 各自保留报告，并附服务器实际数据库完整版本、PG 模式、数据库 CPU 架构和镜像摘要。脚本报告的 architecture 是客户端架构，不能单独证明数据库服务器架构。

新增覆盖：当前版本应用 schema 首次及重复 bootstrap、真实 LangGraph interrupt 后关闭连接并重新建图 resume。本地内存测试只验证场景正确，远端同一场景使用真实数据库；两者证据不可混用。
