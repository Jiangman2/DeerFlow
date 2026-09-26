# 多实例 Gateway 官方补丁追赶与发布门槛（2026-09-24）

本文件区分本地源码、生产实况、候选验证和上线验收。它不是 rollout 指令，也不表示生产已启用 Multiworker。不要用旧 R1/R4 包覆盖当前 Gateway。

## 现网事实

- 2026-09-24 用户回传的只读输出：Gateway `1/1`，Frontend `2/2`，DeerFlow PostgreSQL `1/1`；Gateway 镜像为 `20260923-enterprise-arm64-rc15-channel-pdf-preview-r2`。Frontend 第二副本的登录、对话、消息、渠道、PDF 页面验收已通过。
- `deerflow-runtime-redis` Deployment 和 `deerflow-runtime-redis-auth` Secret 均不存在。SkillHub/WeKnora Redis 是其他应用的资源，不能默认为 DeerFlow 的租约/stream bridge 服务。Gateway 环境变量列表仅见 `DATABASE_URL`，未见 `GATEWAY_WORKERS` 或 Redis 入口。
- 仓库中虽有尚未上线的 `k8s/enterprise/runtime-redis.yaml` 草案，它在原始清单使用 `redis`/`busybox` 标签（`kustomization.yaml` 会锁定镜像 digest），仍是 datax-03 单节点 hostPath 和单副本 `Recreate`。它可作隔离试验的起点，不能直接当作 2,000 活跃任务的高可用 Redis 发布件；先确定镜像供应、独立持久卷/备份、故障切换、密码配置安全和指标告警。完整 enterprise overlay 还包含 Gateway `replicas: 2` 补丁，不得当作仅部署 Redis 的安全命令。
- 先前现网审计显示 `run_ownership.heartbeat_enabled=false`，Gateway/PPT Runner 还使用各自配置及 Python ConfigMap 覆盖。候选镜像必须对比**挂载后的有效源码**、镜像 digest 和配置，而不是只看工作区 `config.yaml`。
- 目前不能宣称跨 Pod 取消、沙箱归属、IM 渠道单活、多实例 scheduler 或 2,000 活跃任务已具备上线能力。

## 已移植到本地源码，尚未打包/上线

| 官方提交 | 本地适配 | 证据与局限 |
| --- | --- | --- |
| [f6269fb / #5757](https://github.com/bytedance/deer-flow/commit/f6269fb6b1f4aa7bd5b8769b5bf340b961d09d65) | Checkpointer/Store 的 SQLite、PostgreSQL 共六条 context 路径在取消时等待 `__aexit__` 完成；退出异常仍可见 | `test_runtime_cancellation.py`、`test_runtime_db_provider_close_cancellation.py`；无数据库迁移或拓扑变化。尚未做真实 PostgreSQL 取消/连接压力测试。 |
| [8f08df9 / #5711](https://github.com/bytedance/deer-flow/commit/8f08df9b48b8788551123b7185f7cf5274023a91) | AIO shell/bash 创建结果不明时保留 tombstone、停止复用、限定请求/补偿超时；Provider release 经既有执行租约和 Redis Ownership 销毁路径回收 | 新增 quarantine 测试，并回归原有 AIO/Provider 测试。企业 Provider 已有大量本地改动；还需候选 Pod 中真实 AIO SDK/沙箱服务与跨进程竞态验收。 |
| [e88599b / #5664](https://github.com/bytedance/deer-flow/commit/e88599bb29a2b47464240da958e292a84eefea2d) | Goal 与回滚共用的同步 Checkpointer helper 对 `put`、`put_writes`、`delete_thread` 排空取消；只读调用仍保持原行为 | 新增三个同步 mutation 取消回归。生产未验证实际同步 saver 类型。 |
| [736b5a4 / #5751](https://github.com/bytedance/deer-flow/commit/736b5a4216c80fd3bd51282d3b57415af94fb16d) | 自定义 Skill 响应读取及回滚中的 Markdown 校验、旧文件读取移出 Gateway 事件循环 | 新增线程归属回归；还需完整的现网 SkillHub/自定义技能页面和阻塞 IO 门禁。 |
| [b6ba739 / #5736](https://github.com/bytedance/deer-flow/commit/b6ba7392976d2848c448b24c23a05d5c388de8e0) | 已实现外部输入拒绝 `sandbox` 通道、AIO/Local Provider 的用户/线程限定查找、工具及 eager middleware 对旧引用的拒绝、`Overwrite` 状态替换；现又接入上游执行级 `SandboxLeaseManager` 至 lead/subagent/tool 生命周期 | 上游 `lease.py` 在企业版缺少 command-scope 接口处作兼容适配；执行级回归与较宽回归通过，但当前只在本地，尚需真实多进程/Redis、取消与重启故障注入，并检查绕开 middleware 的其他 Sandbox 借用路径。不得直接当作多 Gateway 发布包。 |
| [54c65d8 / #5760](https://github.com/bytedance/deer-flow/commit/54c65d8ef) | AIO async acquire 的本地锁路径移出事件循环 | 阻塞 IO 测试覆盖；待候选 Pod 中确认真实锁与远端创建竞争。 |
| [a89cd26 / #5104](https://github.com/bytedance/deer-flow/commit/a89cd2682) | Redis Ownership TTL 验证有限值、正值与毫秒上界，毫秒向上取整 | 边界单测通过；待真实 Redis TTL/PX 压测。 |

上述变更都只在本地工作区适配，**没有生产部署**。当前工作区存在大量其他未提交企业改动；制作候选包时须锁定每个源文件和构建输入，不可把整个工作区原样打包。

企业分支自身还存在一处与跨 Pod 流订阅有关的缺陷：Gateway 的 SSE 断开清理路径引用了未定义的 `gap_emitted`。本地已去除该无效引用，并增加 `cancel`、`continue` 与跨 worker `store_only` 的断开回归；此项不是上表的官方提交，必须单独标记，不能写成“纯上游零差异”。

## 仍需逐项核对、移植或证明等效

| 优先级 | 上游修复/机制 | 当前门槛 |
| --- | --- | --- |
| P0 | [#5736 恢复引用限定到已认证线程](https://github.com/bytedance/deer-flow/commit/b6ba7392976d2848c448b24c23a05d5c388de8e0) | 本地已接入执行级 manager 与取消排空，仍需核对所有绕开 middleware 的 Sandbox 借用路径、管理员只读和分支线程，并在候选环境演练 owner Pod 故障及跨进程租约。 |
| P0 | Redis Sandbox Ownership 后续竞态、跨 worker 持久化取消、DB 事件和多实例 scheduler | 本地有实现和部分单测，生产未完整启用；核对迁移版本、双提交/失权、owner Pod 消失、跨 Pod SSE/取消。 |
| P0 | 海能 Work 渠道连接归属与 Browser Live 状态 | 这两个当前依赖进程局部状态的路径不能由 Redis 沙箱租约自动解决；需单活 leader 或明确只让一个 Gateway 承载其长连接/会话。 |
| P1 | [#5684 readiness 连接关闭](https://github.com/bytedance/deer-flow/commit/ce99b37a674834bfe59a185c07d3d2ca523dc1ae)、[#5755 产物更新取消](https://github.com/bytedance/deer-flow/commit/fc26204debe2808fa1b4064f8fddfea6db2c71f8) | 依次检查企业对应路径及已有等效补丁，按风险和依赖闭包移植。当前企业分支没有上游 `backend/app/gateway/health.py`，#5684 不能直接套用。 |
| 单独评估 | 上游大范围授权与持久化依赖升级 | 需完整 SSO/RBAC、WeKnora/SkillHub、PPT Runner 和旧线程兼容回归；不与第一个多副本候选强制捆绑。 |

## 发布顺序与验收边界

1. 锁定当前四类有效基线：Gateway/Runner 镜像和挂载源码、Frontend、Provisioner/沙箱镜像；备份 Kubernetes 模板、ConfigMap 摘要、数据库迁移版本。不能从本地文件推断生产已启用配置。
2. 为 DeerFlow 建独立 Redis 资源、凭据、AOF/noeviction 和监控，预装其精确镜像与回滚材料；独立验证持久化、重启、失联与密钥注入。生产仍保持单 Gateway。
3. 制作并离线构建候选 Gateway，保留现有渠道/PDF/PPT/企业权限等功能。候选 Pod 带与生产等效的源码覆盖，完成启动、数据库连接、文件/沙箱与页面冒烟。先在隔离环境做真实 Redis/PostgreSQL 和故障注入。
4. 完成海能 Work 单活与 Browser Live 路由后，才把 Gateway 从 `1 Pod × 1 worker` 扩到 `2 Pod × 1 worker`。验证跨 Pod 同一线程、取消、SSE 重连、owner Pod 故障、Redis 暂停/重启、滚动升级和沙箱回收；每一步有明确回退点。暂不增加单 Pod worker 数。
5. 单独扩展监控、PostgreSQL、NFS 和其他单点，并分档压测。8 万是账号总数；2,000 是峰值目标，不是已经认证的活跃任务容量。现有 `scheduler.max_concurrent_runs=3`、单实例 Nginx、单实例 PostgreSQL 与每线程独立沙箱成本均需实际测量和预算。不得把 2,000 在线用户等同于 2,000 同时运行沙箱；按业务流量混合测定准入、排队和降级策略。

2026-09-24 追加的只读集群审计：18 个 ARM64 节点中 datax-01/02/03 与 ecs-zdz-pro-0022 可调度，其余新 ECS 当前均 `unschedulable=true`。StorageClass 全为 `kubernetes.io/no-provisioner`，现有 Redis/PG 卷绑定在旧节点，不能把新 ECS 当成已有动态持久卷或直接让三节点 Redis 落地。0012/0014/0016 各有 32 核、62 GiB 内存；`/opt` 与 `/` 同属 48 GB 根盘，但三台的 `/mnt/data` 均已只读确认挂载在独立 `/dev/vdb1` XFS 盘，每台约 1 TB、空余约 993 GB。Redis AOF/PV 与离线镜像应使用该独立盘，不落在根盘。仍需准备静态 PV/备份、锁定并预装 ARM64 镜像，再按一主两从加故障切换/客户端单入口作隔离演练；现有单副本 `runtime-redis.yaml` 不是正式 HA 发布件。本地候选已给两类客户端加入共用 Sentinel 主节点发现适配，但只通过模拟客户端测试，实际故障切换与异步复制一致性仍未验收，不能只起三个 Redis Pod 就宣称高可用。`lsblk` 对 `MOUNTPOINTS` 列报错仅是旧版工具语法差异，不推翻 `findmnt`/`df` 的挂载证据。额外门槛：现网只读探测证实 `browser_navigate_enabled=True`、`sandbox.replicas=6`。上游明确拒绝在启用 `browser_navigate` 时 `GATEWAY_WORKERS>1`，而企业海能 Work ChannelService 目前在每个 Gateway 进程启动；既要保持浏览器/渠道功能又要扩容，必须先解决浏览器会话归属与渠道单活，或先停在 `2 Pod × 1 worker` 的受控阶段。

当前可交付状态：本地补丁与单测，不是生产热更包。后续包必须重新锁定生产真实基线并完成候选镜像、回滚镜像和故障演练。
