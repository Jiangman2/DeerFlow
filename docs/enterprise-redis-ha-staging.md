# DeerFlow 专用 Redis：三节点预备方案（未部署）

本文件是生产扩容的设计门禁，不是可直接 `kubectl apply` 的清单。现网没有 `deerflow-runtime-redis` Deployment 或认证 Secret；现有 `k8s/enterprise/runtime-redis.yaml` 是 datax-03 上的单副本、未锁镜像草案，不应直接用于多 Gateway。

## 已有只读证据（2026-09-24）

`ecs-zdz-pro-0012`、`0014`、`0016` 各有 32 vCPU、约 62 GiB 内存。三台的 `/mnt/data` 均为独立 `/dev/vdb1` XFS，约 1 TB、可用约 993 GB；`/opt` 属于 48 GB 根分区。节点目前处于 cordon。StorageClass 是静态 `kubernetes.io/no-provisioner`，不能假设自动分配 PV。`lsblk -o MOUNTPOINTS` 报错是旧版命令列名差异；`findmnt -T /mnt/data` 和 `df -hT` 已确认当前挂载。

三台的 `/etc/fstab` 均使用各自 UUID 为 `/mnt/data` 声明 `xfs defaults,nofail`。`blkid` 与 fstab 的 UUID 已逐台一致；这证明配置了开机挂载尝试，**不证明重启后一定挂载成功**：`nofail` 允许缺盘时继续启动。`containerd config dump` 显示三台 containerd root 都是 `/mnt/data/containerd`。进一步只读审计证实 containerd 和 kubelet 的 `RequiresMountsFor`、`BindsTo` 均为空；containerd 只有 `After=local-fs.target`，不足以覆盖 `nofail` 缺盘情况。若数据盘缺失时 containerd 或 Redis local PV 意外使用根盘同名目录，会造成镜像/租约数据分叉和 48 GB 根盘耗尽。因此在创建 PV 或解除 cordon 前，必须给 containerd、kubelet/Redis Pod 建立可验证的挂载点失败关闭门禁；在隔离节点演练缺盘或失败挂载后，两者不会写入根盘。候选 systemd 依赖可考虑 `RequiresMountsFor=/mnt/data` 加 `BindsTo=mnt-data.mount`、`After=mnt-data.mount` 和 `AssertPathIsMountPoint=/mnt/data`；这些指令的最终组合要按本机 systemd 版本、现有 unit 与实际 Pod 清单在一台隔离节点验证，不得直接批量重启三个节点。

最初的只读 Pod 审计确认三台均为 `Ready,SchedulingDisabled`，每台仅有 Flannel 与 kube-proxy DaemonSet，无业务 Pod；安装挂载门禁后仍须重新进行实时 K8s 复核。`scripts/enterprise_redis_mount_guard.py` 默认只检查；显式 `--apply --node <本机名>` 时才为对应节点的 containerd/kubelet 安装精确 systemd drop-in、执行 `daemon-reload`，并核实依赖加载且 MainPID 未变化。脚本不会重启服务、解除 cordon、创建 PV 或部署 Redis。安装成功也只表示**配置加载**，不代表缺盘失败关闭已通过演练。只有在计划维护窗口、备份和恢复路径就绪、确认业务 Pod 仍为零后，才能单独安排隔离节点的缺盘故障演练；不可直接拔盘或卸载运行中的 `/mnt/data`。

0012 试点记录：最初上传脚本 SHA256 为 `078c77136fabcfb6b56b215a71fac80ed7ad6b0a7b379563fa2f0e6772917d66`；只读检查回报 `PREFLIGHT_PASS`，`--apply` 回报 `DROPINS_LOADED`，新建的两个 drop-in 分别属于 containerd 和 kubelet，服务 MainPID 不变。应用后的 K8s 复核仍为 `Ready,SchedulingDisabled`，Flannel 与 kube-proxy 均运行，未发现业务 Pod。正常重启后，`/mnt/data` 仍挂在 `/dev/vdb1` 且 containerd、sshd 正常，但 kubelet 为 `inactive`，节点一度 `NotReady`。`systemctl show` 显示 `UnitFileState=disabled`、`Result=success`，本次开机 journal 没有 kubelet 启动记录；手动 `systemctl start kubelet` 后服务为 `active`。这是重启前检查漏掉 kubelet 自启状态所暴露的独立问题，不能把它误判为数据盘缺失或挂载门禁失败。此后 K8s 已复核 0012 恢复 `Ready,SchedulingDisabled`；**缺盘失败关闭仍未演练**。

0012 的 kubelet 已单独设为 `enabled` 且保持 `active`；尚未再次重启。随后只读对照发现 0014/0016 的 containerd 都为 `enabled,active`，kubelet 原为 `disabled,active`：这是三台新节点共有的开机自启缺口。用户已回报 0014/0016 分别执行 `systemctl enable kubelet` 后均为 `enabled,active`；这两台尚未重启。

本地脚本已增补 `r3-per-node-uuid-boot-enable-preflight`（SHA256 `437932933cc981977a85535f8415b9d2446e6d0beea3f782624ac6f4f8d8814f`）：仅支持 0012/0014/0016 的各自 UUID，安装或复核前要求 containerd/kubelet 的 `UnitFileState=enabled`，否则明确报错并停止；`--apply` 还必须带匹配本机 hostname 的 `--node`。只读检查会拒绝部分安装，并验证已加载的完整依赖。此前上传到 0012 的脚本仍是旧 SHA，不能将其检查结果当作新门禁。任何下一次重启前，先核实服务开机自启、挂载依赖、控制台可用性以及 node cordon/Pod 清单。`systemctl enable` 只创建开机启动链接并重新加载管理器，不应与 `--now` 或重启混用。

0014 和 0016 的 r3 脚本均已校验 `OK`，只读预检均为 `PREFLIGHT_PASS`；各自带精确本机名的 `--apply` 均回报 `DROPINS_LOADED`，两个服务 PID 未变化。安装后再次从 datax-01 实时核查：0012/0014/0016 全部 `Ready,SchedulingDisabled`，每台只有运行中的 Flannel 与 kube-proxy Pod。0012 使用的是先前已安装的同一门禁内容；三台均**未**做缺盘故障演练。注意 r3 输出的 `node_uncordoned=false`、`redis_deployed=false` 是脚本静态说明值，**不是实时 K8s 查询**，不能用它们代替 `kubectl get node/pods`；`installed_units=[]` 是安装前已有文件清单，不是安装后状态。后续本地 r4 修正了观察字段为 `kubernetes_state=NOT_CHECKED`、`redis_state=NOT_CHECKED` 及安装前/后列表，但此 r4 尚未上传或用于三台节点；不能用本地 r4 的测试结果冒充生产复核。

生产现有 WeKnora Redis 使用镜像 `m.daocloud.io/docker.io/library/redis@sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2`，SkillHub Redis 是另一自建镜像。datax-03 的 containerd `k8s.io` 命名空间已只读确认该精确 digest 引用存在；此前的 `MISSING` 是命令中的镜像引用被聊天链接截断至 `@sha256` 所致，并非镜像缺失。可评估复用前者的**镜像字节**作为专用 DeerFlow Redis/Sentinel 候选，但不能复用 WeKnora 或 SkillHub 的 Redis 服务、数据、密码或容量。该 digest 在新节点上的离线可用性、Redis 版本及真实 Sentinel 行为尚未验证。集群有 `deerflow-redis-local` 的静态 `no-provisioner`/`Retain`/`WaitForFirstConsumer` StorageClass；这不代表已有专用 PV/PVC。候选部署应单独命名存储资源，核对 PV 的 `nodeAffinity` 和磁盘实挂载后才可调度，避免与现有 Redis 数据混用。

datax-03 已从上述精确镜像引用按 `linux/arm64` 离线导出 `/opt/deerflow-build-artifacts/20260925-runtime-redis-ha-candidate/redis.arm64.oci.tar`，`sha256sum -c` 回报 `OK`。containerd 的两条 cgroup v1 弃用提示不是导出失败。第一次由 `cnooc` 发起的复制未执行：源目录不可读取，命令中的 `test -r && scp` 静默短路；改用 `sudo scp` 从同一归档复制后，0012 `/mnt/data/deerflow-runtime-redis-image-stage/20260925/redis.arm64.oci.tar` 已收到 14,086,144 字节，目标侧 SHA256 对比回报 `redis_0012_transfer=PASS`。0012 随后按 `linux/arm64`、`--no-unpack` 导入；精确 digest 引用在 containerd 与 CRI 均为 `PRESENT`，`redis_0012_image_import=PASS`。0014/0016 已各自复制同一归档，SHA256 校验分别为 `redis_0014_archive=PASS` 和 `redis_0016_archive=PASS`；同样的 `--no-unpack` 导入后，containerd 与 CRI 对精确 digest 均检查通过，`redis_0014_image_import=PASS`、`redis_0016_image_import=PASS`。三台都仍处于 cordon，尚未创建 Redis 容器/PV/PVC/Secret，也未改动 Gateway。不要把镜像导入成功当作 HA 成功。

下一项门禁是在隔离的 0016 上执行一次性 `redis-server --version` 容器，确认镜像不仅可被 CRI 枚举，而且能从当前 XFS containerd root 解包并启动。该测试不开放 Redis 端口、不创建 Kubernetes Pod、不解除 cordon；若失败需先检查是否残留测试容器，不得盲目重试。通过后仍需分别验证 Pod 的真实启动、持久卷挂载、三节点复制与 Sentinel 故障切换；镜像版本和架构以一次性容器实际输出为准。官方 Sentinel 文档要求配置文件可写、至少三个独立故障域，并说明异步复制不能保证故障时保留已确认写入；因此它不是沙箱租约安全性的充分证明。参见 <https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/>。

0016 首次一次性容器测试已打印 `Redis server v=7.0.15`；随后用户在同一终端首先查询上一条命令，回报 `last_exit_code=0`，进程检查无运行中的测试命令，containerd 容器清单没有 `deerflow-redis-smoke-0016`。因此该镜像在 0016 上的**一次性启动/退出及清理**可以判为通过，但原脚本末尾的自定义 `redis_0016_execution=PASS` 未见于回传，不能以此声称其余节点或 Kubernetes Pod 已验证。不要重跑或删除生产容器。

版本发现同时改变了候选决策：这份复用 WeKnora 字节的镜像是 Redis **7.0.15**，不应直接晋升为新的生产 HA 基线。Redis 官方受支持版本列表未列出 7.0；2026 年安全公告要求相应受支持分支的修复版本。下一步先锁定受支持的精确 ARM64 版本、OCI digest/供应链来源，再离线导出并在三台节点重新校验、预装和一次性启动；旧 7.0.15 仅作为已完成的可行性实验，不创建承载生产租约的 Pod。参见 <https://redis.io/docs/latest/operate/oss_and_stack/install/version-mgmt/> 和 <https://redis.io/blog/security-advisory-cve-2026-81934/>。

候选版本限定为 Docker 官方 ARM64 `redis:7.2.16-bookworm`。Redis 官方受支持列表列出 7.2 为 Extended；2026-08 安全公告把 7.2.16 列为该线的修复版本。官方许可说明指出 Redis 7.2.x 及以前为 BSD-3-Clause，7.4.x 改为 RSALv2/SSPLv1；因此在没有企业法务确认前，优先选 7.2.16，而非刚发现可下载的 7.4.11。已停止尚未完成的本地 7.4.11 拉取，不在服务器部署它。参见 <https://redis.io/legal/licenses/> 与 <https://github.com/docker-library/official-images/blob/master/library/redis>。

后续本地离线制品已完成：Docker 官方 `redis:7.2.16-bookworm` 的 `linux/arm64` 拉取显示多架构索引 digest `sha256:0637954999d01b7c9ce9167db2da50656e2590d3b884f1c600c5f63bb6e6773c`；本地隔离、无网络容器的 `redis-server --version` 打印 `7.2.16` 并以 0 退出。使用 `docker image save --platform linux/arm64` 导出 `deerflow-runtime-redis-7.2.16-arm64-20260925.oci.tar`（45,519,872 字节）；归档索引恰有一个 `linux/arm64` 镜像，manifest digest 为 `sha256:645c0e8f812b62f309b071d7d7f9c6f573b829f91c9e7babbd84972b0a67ca4a`。归档内 13 个 OCI blob 的 SHA256 均按路径摘要核验通过；完整归档 SHA256 为 `a89df132025d0122a6f403640749945d3aa8b749b604122eb8776cbabd55c92d`，旁置 `.sha256` 文件供服务器再次校验。索引另有一个未标注平台的 provenance/attestation manifest，不得误认为第二种运行架构。旧 7.0.15 预装仅作为隔离验证记录，不应创建生产 Redis Pod。由于多架构索引 digest 与平台 manifest digest 不同，后续服务器导入必须用实际 `ctr images list`/CRI 检查精确镜像引用，再据此锁定部署清单，不可把索引 digest 当作本地 manifest digest。

用户随后把 7.2.16 归档和 `.sha256` 上传到 `datax-03:/home/cnooc/`，在该节点运行 `sha256sum -c deerflow-runtime-redis-7.2.16-arm64-20260925.oci.tar.sha256` 回报 `deerflow-runtime-redis-7.2.16-arm64-20260925.oci.tar: OK`。这仅证明 datax-03 上的归档传输完整；0012/0014/0016 的 7.2.16 导入、启动和 Kubernetes 演练仍待进行。先在保持 cordon 的 0016 单节点验证，不能凭此回报解除 cordon 或触碰 Gateway。

## 候选拓扑

| 节点 | 角色 | 持久化位置 |
| --- | --- | --- |
| 0012 | Redis 数据副本 + Sentinel | `/mnt/data/deerflow-runtime-redis/0012` |
| 0014 | Redis 数据副本 + Sentinel | `/mnt/data/deerflow-runtime-redis/0014` |
| 0016 | Redis 数据副本 + Sentinel | `/mnt/data/deerflow-runtime-redis/0016` |

具体主节点由演练时决定；不能把主节点永久绑定到某台机器。每台一个静态 `local` PV/PVC，明确 `nodeAffinity`、Pod anti-affinity、requests/limits、PDB 和 ClusterIP-only 网络策略。Redis 使用精确 ARM64 镜像 digest 和离线预装；AOF、`appendfsync everysec`、`maxmemory-policy noeviction`、专用数据库/前缀、独立认证 Secret，并验证冷启动恢复与备份恢复。AOF everysec 和 Redis 异步复制不是零数据丢失保证，必须把故障切换窗口作为所有权一致性测试的一部分。

三台 Sentinel 的仲裁数拟为 2，但**三台 Redis Pod 不等于 HA**。本地候选已给 StreamBridge 和 Sandbox Ownership 增加共用的 `sentinel://host1:26379,host2:26379,host3:26379/deerflow/6?password=<URL 编码值>` 解析与主节点发现适配；原有 `redis://` 仍可用。这是企业适配，不是已上线的官方功能。密码应通过 Secret 注入环境或配置，不写入仓库和日志。现阶段仅通过客户端构造及现有模拟 Redis 测试；上线前仍须验证真实 Sentinel 故障切换、断线重连、旧主隔离、租约到期与续租、SSE 断点重播。客户端在新主身份不明时应失败关闭，不能写入可能已失权的旧主。Redis 异步复制还可能丢失已确认的租约写入；发现主节点不等于解决这一一致性问题。

## 放行顺序

1. 只读确认三台节点的 `/mnt/data` 当前挂载及 UUID、containerd root、现有标签/taint 和本机防火墙策略；先不 uncordon、不创建目录。针对 `nofail` 设计并演练缺盘时 kubelet/Redis 不启动的持久门禁。
2. 锁定 Redis/Sentinel 精确镜像与配置，离线预装到三台；在候选命名空间创建静态 PV/PVC、Secret、数据 Pod、Sentinel 与可观测性。生产 Gateway 仍单副本。
3. 将同步 Ownership 客户端与异步 StreamBridge 客户端切换到相同的、可自动发现主节点的端点；用真实 Redis 测主切、网络分区、重启、AOF 恢复、密码轮换、故障时拒绝新沙箱归属和 SSE 重连。
4. 锁定生产 Gateway 的**有效镜像和挂载源码**后构建候选，预装候选及回滚镜像，完成隔离 Pod、跨 Pod run/取消/沙箱测试；海能 Work 单活和 Browser Live 路由仍是额外门禁。
5. 只有所有门禁通过，才规划 `2 Pod × 1 worker` 灰度；现网 `browser_navigate_enabled=True`，因此单 Pod 多 worker 仍被官方启动门禁禁止。`sandbox.replicas=6` 是每进程容量，扩副本前须重新核算节点/NFS/磁盘预算。

8 万是账号总数，2,000 是峰值目标；该目标还需要实际流量混合、排队/准入、PostgreSQL/NFS/Nginx 和 Sandbox 成本压测，不能由 Redis 节点数或 Gateway 副本数直接推定。

## 官方后续修复审计（本地候选；不代表生产已上线）

2026-09-25 对 `upstream/main` 的定向审计发现：官方 `a89cd2682` 的 Redis 所有权租约 TTL 向上取整与溢出校验、`8f08df9b4` 的不确定 AIO session 创建隔离回收、`54c65d8ef` 的异步锁路径线程卸载、`509151d00` 的 SQLite scheduler 写锁读取，在本地企业候选代码中均可找到对应实现。必须继续逐项跑回归，并锁定候选镜像内容，不能仅凭源码存在推定线上已应用。

官方 `842f56105` 的 Agent 流关闭期间延迟宿主取消修复仍未合入本地 `runtime/runs/stream_cleanup.py`；现有实现直接 await `aclose()`，并且 run worker/subagent 关闭路径尚未具备该补丁的取消传播与日志处理。它属于多实例取消可靠性门禁，须连同官方测试按当前企业 worker 结构适配后再构建候选。官方 `e68b4e14e` 的空白 `GATEWAY_WORKERS` 与 Lark broker 配置处理在本地基线结构不同，不能直接 cherry-pick；需要先确认受影响代码是否存在，再决定移植。未经候选验证不得修改生产 Gateway。
