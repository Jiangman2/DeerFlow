# 第一批扩容：0015 前端接入准备 R1

2026-09-24。这是**准备工具，不是生产 rollout 包**。目标是让第一台新节点
`ecs-zdz-pro-0015` 具备承接现网 Frontend 第二副本的条件。

本包不扩 Gateway、不升级业务镜像、不改数据库/NFS、不更改 Secret、
不解除节点 cordon、不创建 Pod、不重启服务，也不执行任何 Kubernetes 写操作。
不把准备成功、预装成功或静态测试成功解释为 2000 并发验收。

## 现在执行：只读两项

将压缩包和外部 `.sha256` 文件上传到 **datax-01、ecs-zdz-pro-0015 的
`/home/cnooc/`**。这是约几十 KiB 的控制工具，不是大型镜像包。
先由 cnooc 用户在两台机器分别解压；已有同名目录就停止，不覆盖。

```bash
cd /home/cnooc
sha256sum -c deerflow-capacity-wave1-prepare-20260924-r1.tar.gz.sha256 &&
test ! -e deerflow-capacity-wave1-prepare-20260924-r1 &&
tar -xzf deerflow-capacity-wave1-prepare-20260924-r1.tar.gz &&
cd deerflow-capacity-wave1-prepare-20260924-r1 &&
sha256sum -c PACKAGE.SHA256
```

仅在 **datax-01** 执行：

```bash
cd /home/cnooc/deerflow-capacity-wave1-prepare-20260924-r1
sudo python3 -B ./prepare-wave1.py cluster \
  --output "/home/cnooc/wave1-cluster-$(date -u +%Y%m%dT%H%M%SZ).json"
```

仅在 **ecs-zdz-pro-0015** 执行：

```bash
cd /home/cnooc/deerflow-capacity-wave1-prepare-20260924-r1
sudo python3 -B ./prepare-wave1.py node \
  --output "/home/cnooc/wave1-node-0015-$(date -u +%Y%m%dT%H%M%SZ).json"
```

返回 stdout 及生成的两份 JSON。无需发 Secret、kubeconfig、完整环境变量。
报告只有镜像/配置摘要、环境变量名称、资源和调度信息；不含环境变量或密钥值。
`cluster` 包含一次在现有前端容器中读取 Next.js 构建指纹的只读命令。
`node` 读取运行中的 containerd 配置确认真实 root，不靠旧记录推测。
报告是新建文件，0600，sudo 调用时归还给调用用户；不覆盖旧文件。

若 BLOCKED，发结果，不去掉检查、不盲目重跑、不手动 uncordon。
现网有其他发布进行中也会停止，以免拿错基线。

## 随包能力：审阅结果后才进入镜像预装

目前固定允许的前端镜像是
`deerflow.local/deer-flow-frontend:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2`。
镜像标签不同需要重新审阅，而不是修改脚本中的白名单强行通过。
具体镜像 digest 来自 Ready 现网 Pod，不从本地开发分支推断。

1. `export --plan <cluster报告> --ack FRONTEND_IMAGE_ONLY --output <新报告>`
   仅允许在报告的 `frontend.source_node` 上运行。现场报告过去显示 datax-02，
   但必须以新的采集结果为准。工具要同时上传该节点。
   `/opt/deerflow-build-artifacts` 必须已存在且位于独立数据盘。
   以 nice/ionice 低优先级导出现网同版 ARM64 镜像，不导出其他历史镜像，
   不构建、不拉取、不删除。目录为
   `/opt/deerflow-build-artifacts/20260924-capacity-wave1-r1/`。
2. 传送 `frontend.current.oci.tar`、`image-receipt.json` 和同一份 cluster 报告到 0015。
   镜像存放于 `/mnt/data/deerflow-image-stage/20260924-capacity-wave1-r1/`。
   不放 `/home/cnooc` 或 `/opt`：0015 的 `/opt` 不是独立大盘。
   目标父目录需运维显式创建并授权 cnooc 上传，不广泛 chmod，不修改 containerd 目录权限。
3. `preload --plan <同一cluster报告> --ack FRONTEND_IMAGE_ONLY --output <新报告>`
   仅允许在 0015 执行。校验归档 SHA、OCI 元数据、镜像身份及 ARM64 架构；
   以 `--no-unpack` 导入并标记 CRI managed/pinned。发现同标签不同 digest 就停止，
   不覆盖。不调整 image GC 阈值、不清理缓存、不重启 containerd/kubelet。
4. 导入只是元数据和镜像内容准备；解包、实际容器启动、DNS、Service 连通、
   Next.js 页面/SSO/API/文件/静态资源验收仍须通过隔离 canary。
   该 canary 和生产上线动作**不包含在本准备包中**。

每个模式仍要求 `--output`，并且生产镜像操作使用的 cluster 报告不得超过 6 小时。
一次导出和导入使用同一报告；超时不要手改时间戳或删除现有归档，先审阅恢复路径。
导入/导出每 20 秒有进度心跳，最长 30 分钟；超时不等于已恢复所有 runtime 状态，
须检查后再重试。SHA 校验会读取整个归档，执行中不会删除归档。

准入下限：0015 根盘可用 >=8GiB 且使用率 <=80%；独立数据盘和实际 containerd root
可用 >=40GiB 且使用率 <=75%；MemAvailable >=4GiB。它们只是这一次前端预装的保守门槛，
不是整套平台容量公式。CPU 采样随报告返回，不由五分钟/历史负载直接推断可承载量。

## 隔离验证、上线与回退的下一步约束

- 审查目标上的其他 Pod、可分配资源、污点和网络策略，不能直接全量 uncordon 放开调度。
- Canary 使用不匹配生产 Service 的独立标签；验证前不得进入生产 EndpointSlice。
- 复用完全相同的构建、SSO 运行配置和挂载，验证 BUILD_ID、Server Actions
  密钥的一致性和缓存/会话语义；不要为“扩副本”重建一个不同版本前端。
- 生产前端到 2 副本时保留至少一个旧 Ready 副本，限制同一节点共置；
  不新建与原 Deployment 选择器重叠的第二个控制器。
- 失败时只撤回本批新增对象/精确变更；不动 Gateway、渠道、数据库、Runner、NFS。
  真正 rollback 命令须绑定实施时的对象 UID/规格和保存的变更前状态；本包不伪造可执行回退。
- 第二前端副本是一个容错/入口容量步骤，并不提升单 Gateway 的 Agent 执行能力，
  更不解决单入口或 NFS 单点。

官方依据：[Next.js 自托管多实例注意事项](https://nextjs.org/docs/app/guides/self-hosting)、
[Kubernetes 节点选择](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)。

## 验证范围

- 本地 Python 3.11：40 项自动化测试通过；覆盖 mock 集群完整流程、红线/基线竞争、
  脱敏、OCI 归档、导出/预装调用约束和 LF 文件。
- Python 3.6 语法 AST 检查通过；运行程序仅使用 Python 3.6 标准库可用接口。
- 当前 Docker Desktop Linux Engine 未运行；未声称完成真实 Linux/containerd/K8s 集成验证。
- 未连接生产、未修改生产、未执行真实预装、未进行登录浏览器验收。

## 后续 Multiworker（不混入本包）

旧包不能直接覆盖当前 channel-pdf/PPT/ConfigMap 源码挂载基线。
Redis Ownership、持久化跨进程取消、scheduler、DB 事件和 Redis SSE 必须作为一组验证。
还要分别解决 Browser Live 的进程内会话、海能 Work 的单消费者、Gateway/Runner
共同使用沙箱的租约，以及失权/Redis 切换后旧 owner 不得回写或误删。
只有通过这些门槛才发布 2 Pod × 1 worker 候选；不能以新增服务器代替正确性修复。

8 万账号、2000 在线连接、2000 活跃 run、2000 重型沙箱任务分别测量。
上线任务并发上限取实测 Gateway/数据库/模型配额/存储/沙箱池中最小的可承载量并留余量；
超出者提供可见排队和取消，不把排队数量计入正在执行的并发。
