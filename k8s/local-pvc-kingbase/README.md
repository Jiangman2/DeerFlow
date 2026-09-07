# KingbaseES V009R001C010B0004 独立数据库部署

当前是待实机验收的候选配置。依据用户交付的两份 Docker 归档解析生成，校验值和镜像配置摘要见 ../../docs/upstream/vendor-images.json。它只新增金仓数据库，不修改或删除现有 PostgreSQL、WeKnora，也不自动切换应用连接。

## 已核实镜像契约

| 架构 | 镜像内标签 |
| --- | --- |
| amd64 | kingbase_v009r001c010b0004_single_x86:v1 |
| arm64 | kingbase_v009r001c010b0004_single_arm:v1 |

运行用户 1000:1000，安装目录 /home/kingbase/install/kingbase，端口 54321。两个架构入口脚本内容相同。initdb 帮助文本明确支持 --pwfile、-m pg、-A；版本文件为 SYS_VERSION，认证配置为 sys_hba.conf，备份客户端使用 KINGBASE_PASSWORD。

候选启动器直接运行厂商 initdb 和前台 kingbase，不运行原入口的 cron 与永久 sleep。密码从 Secret 文件读取，不使用原入口的 eval 或默认密码。许可证以 Secret 文件覆盖 bin/license.dat。该运行方式及许可证有效性仍须实机验证，不能仅凭镜像静态检查认证。

## 内网准备

1. 将相应 Docker.tar 导入每个允许调度数据库及备份 Job 的节点。Docker 节点可用 docker load -i；containerd 节点应按集群规程导入 Kubernetes 使用的镜像命名空间。核对归档 SHA-256、架构及标签。
2. 已有 deer-flow 命名空间与可用 StorageClass；本配置创建独立 PVC。不能把旧 PostgreSQL 卷绑定给金仓。资源请求是候选起点，不是容量测算。
3. 运维以安全文件提供 DB_USER 和 DB_PASSWORD，并建立名为 deer-flow-kingbase 的 Secret。DB_PASSWORD 文件只包含实际密码；避免在命令行内写明密码。
4. 从有效授权文件建立 deer-flow-kingbase-license Secret，键名 license.dat。不要把许可证和密码文件加入 Git。Secret subPath 更新后需重新创建数据库 Pod 才能使用新许可证。
5. 选择 overlays/amd64 或 overlays/arm64，先渲染审查，再 apply。imagePullPolicy=Never 要求镜像已导入；脚本以 PG 模式、UTF8、SCRAM 初始化。Service 仅 ClusterIP，按企业网络策略限制数据库访问。

```sh
kubectl kustomize k8s/local-pvc-kingbase/overlays/amd64
kubectl apply -k k8s/local-pvc-kingbase/overlays/amd64
```

arm64 使用相应目录。Kubernetes 管理前台进程；停止通过 sys_ctl fast 完成，给出 120 秒终止窗口。新库启动失败时检查 Pod 事件和数据库日志；不删除有数据的 PVC 来重试。

## PoC 与应用接入

数据库成功启动不代表应用兼容。由运维创建独立 deerflow_kingbase_poc_* 数据库，以项目同版本后端环境执行 scripts/kingbase/README.md 中的验证；将脱敏报告回传。分别记录数据库服务器的 CPU 架构，不要拿客户端架构代替。

生产数据库与 PoC 库分开。应用仍使用 backend: postgres；是否需要进一步驱动/方言适配由 PoC 决定。不要在 PoC 通过前切换 DATABASE_URL。历史应用数据、LangGraph 数据及迁移元数据必须全部迁移，并验证恢复。原 PostgreSQL PVC 保留用于切换前回退；开放金仓写流量后不能承诺无损切回。

## 备份与恢复

operations 中按架构提供作业；先应用 backup-pvc.yaml，再选对应备份 CronJob。CronJob 默认 suspend=true，必须在确认 DB_NAME、成功执行一次备份及恢复演练后由运维启用。存储访问模式为 RWO，跨节点使用需符合实际 StorageClass 限制。

备份使用 sys_dump 自定义格式，先写 .partial，成功并通过 sys_restore --list 后原子重命名；目录列表校验不是恢复验收。默认不自动删除历史备份。恢复 Job 仅接受运维预创建的空 deerflow_restore_check_* 库，禁止 drop/clean；填入明确备份文件名，执行事务恢复并核对基本业务表。恢复时必须结合升级手册继续完成完整数据及历史会话验收。

所有操作脚本保持真实退出码；失败禁止通过 tail/head 管道或 shell fallback 掩盖。备份与恢复 YAML 内嵌脚本，修改 operations/*.sh 时必须同步对应 YAML 并通过清单测试。

初始化完成后才生成 .deerflow-init-complete 标记。初始化中断、缺失配置或来源不明的旧数据目录会拒绝启动；不得手工创建标记绕过检查。恢复应在本启动器已经成功初始化的隔离库中进行逻辑恢复。
