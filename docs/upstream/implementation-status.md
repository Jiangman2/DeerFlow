# 实施验收状态

目标：企业分支可持续同步官方，DeerFlow 主库适配 KingbaseES V9R1C10，分别验证 x86_64/ARM64。达梦及 WeKnora 数据库改造排除在范围外。

| 要求 | 当前证据 | 状态 |
| --- | --- | --- |
| 固定上游基线和同步流程 | baseline.json、upgrade-runbook.md；区分缓存和认证版本 | 已实现记录机制，未完成新版本同步 |
| 核心差异清单 | inventory.json：424 个共享文件，13700 个企业新增文件 | 清单生成完成，逐项审查待做 |
| 数据库敏感变更检测 | persistence-watch.json、CI workflow、本地漂移测试 | 本地通过，远程 CI 未执行 |
| 金仓实际协议/SQL/检查点/Store/应用建库验证 | scripts/kingbase/probe.py；本地场景测试 | 工具就绪，真实环境未运行 |
| 精确版本与架构 | 用户指定 V9R1C10，x86_64/ARM64 | 尚无服务器实测证据 |
| 应用兼容配置与最小适配 | 已新增通用 postgres_maintenance_database；默认 postgres | 进一步兼容差异待实测 |
| 独立 K8s 部署、探针、授权、备份恢复 | 已生成双架构数据库、暂停备份与隔离恢复清单 | 静态验证通过，运行待验收 |
| 历史数据迁移与恢复 | 升级手册定义验收要求 | 未实施、未验证 |
| 生产发布认证 | certification_status=not_verified | 未完成 |

## 当前外部条件

用户提供了 V009R001C010B0004 两份 Docker 归档，已解析架构、镜像标签、启动脚本、用户和数据库工具，并记录 SHA-256。依据镜像证据创建了独立部署候选和运维任务。

企业内网服务器无法由本任务登录。改由企业运维按 k8s/local-pvc-kingbase/README.md 执行部署及 scripts/kingbase/README.md 中的 PoC，再回传脱敏报告。本机 Docker Linux daemon 当前未运行，因此没有本地数据库启动证据。测试通过不代表数据库认证。

代码和清单尚未合并或部署。原有未跟踪的企业部署文档不属于本次修改。

本地已按 backend/uv.lock 补齐 postgres extra；Docker Desktop 启动尝试未留下运行进程，引擎未就绪，未创建测试容器。新增 4 项实际 shell 初始化失败/重启保护测试通过；仍不构成金仓实机证据。
