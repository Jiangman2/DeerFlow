# 数字员工共享：离线候选镜像构建，不是热更

本包只含 46 个 Gateway、33 个 Frontend、1 个 Provisioner 的已审阅源文件增量，以及构建控制程序。**不含上线、回退或镜像导入指令，不能用于 rollout。** 现有员工、数据库、技能库和运行中工作负载不在其写入范围内。

2026-09-24 的存储及源码覆盖报告确认：生产 Gateway 有 15 个固定 Python 源码挂载，
PPT Runner 另有 4 个独立挂载。新版本把这些来源证据固定到 `source-overlays.json`；
Gateway 构建阶段在精确 OCI 底座上先重现其 15 个实际运行文件，再核对/应用员工增量。
其他组件的镜像不混入这些补丁，PPT Runner 的独立补丁也不复制进 Gateway。
未知挂载依旧阻断，没有跳过选项。**旧 v3 保持不变并停用；本包仍不是热更包。**

来源检查记录 ConfigMap 的真实资源名、UID、resourceVersion、键和字节摘要，不把 volume
名误当资源名。构建前/过程中只读复查四个 Deployment、实际 Pod 和这些 ConfigMap；
每个新容器首次检查及构建完成前，使用固定哈希程序验证挂载字节，以发现 stale subPath。
普通周期检查不重复执行容器哈希。ConfigMap/模板/镜像变化、Pod 未就绪或未知源覆盖均停止。
检查只读取挂载源码，不读取 Secret、环境值或员工数据，不修改生产对象。
这仍是有限采样，不是集群原子快照；构建成功后发布前也必须重新检查。
源码差异可以被重现，不代表已完成依赖闭包：源校验、真实导入及前端编译必须全部通过。

## 输入与权限

必须使用三个真实生产底座的完整本地 OCI 归档，而不是本地开发镜像、同名其他版本或手工改 tag。固定文件名为 `gateway.base.oci.tar`、`frontend.base.oci.tar`、`provisioner.base.oci.tar`。来源镜像名称、运行时 ID 和应用字节锚点见 `CANDIDATE.json` 与三个 `*-delta.json`；任何不符立即停止。CRI ID 可能是 config 或 manifest 的摘要，不假设它一定是 manifest。

归档准备和最终执行均由管理员在服务器手动进行，本地开发端不连接生产服务器。先确认 Gateway 已在其他节点、没有构建/导入任务，再从现存归档复用或串行导出精确底座。构建器不会自行调用 containerd 导出，不会拉取不存在的镜像。导出本身仍有磁盘 I/O，不属于本构建器的限额保证；不要和生产故障恢复或其他导入同时执行。

包目录、归档目录及输出父目录必须位于独立挂载的 `/opt`，root 所有、不可由组或其他用户写入、无链接。输出目录必须不存在。不要把归档、日志或生成结果放进不可变包目录。输入归档按敏感内部镜像保管，不要上传到公共站点。

## 执行边界

仅支持 root / ARM64 / `datax-01`，需要已安装的 BuildKit、systemd、kubectl 和 Python 3.7+。不安装软件、不改现有 BuildKit 服务。命令模板（路径由发布协作时确认后替换）：

```bash
sudo python3 -B /opt/deerflow-hotfixes/EMPLOYEE_BUILD_INPUTS/verify_candidate.py \
  /opt/deerflow-hotfixes/EMPLOYEE_BUILD_INPUTS
sudo python3 -B /opt/deerflow-hotfixes/EMPLOYEE_BUILD_INPUTS/build_offline.py \
  --base-archives /opt/deerflow-build-artifacts/EMPLOYEE_EXACT_BASES \
  --output /opt/deerflow-build-artifacts/EMPLOYEE_NEW_BUILD_OUTPUT \
  --check-only
sudo python3 -B /opt/deerflow-hotfixes/EMPLOYEE_BUILD_INPUTS/build_offline.py \
  --base-archives /opt/deerflow-build-artifacts/EMPLOYEE_EXACT_BASES \
  --output /opt/deerflow-build-artifacts/EMPLOYEE_NEW_BUILD_OUTPUT \
  --build-only-ack YES
```

禁止绕过失败门禁，也不要直接执行 Dockerfile，避免漏传本地 OCI context 后尝试拉镜像。此模板没有授予生产部署修改权限。

`--check-only` 只做包、路径、现有归档权限和集群/主机只读检查（含固定源码哈希读取），不启动 unit、不导出镜像、不新建输出目录。输出的缺失底座列表不是“镜像通过”；实际 cgroup 限额及完整 OCI 内容仍要在受限构建阶段验证。

- 唯一、短期的 systemd slice 同时覆盖守护进程、辅助进程和 RUN 进程：CPU 150%、内存 12 GiB、TasksMax 2048、`/opt` 所在块设备读 80 MiB/s、写 40 MiB/s，BuildKit 并发 1。
- 从 cgroup 内核文件核对配额；另执行短暂、无缓存的真实 RUN，宿主观察其 PID 必须处于全部受限控制器中。旧 systemd、缺失控制器或限额未生效均阻断，不降级为仅 nice/IO 优先级。
- cgroup v1 不能假设子组继承 I/O 限额。守护进程、当前辅助进程和唯一 RUN 分别设为总 I/O 预算的三分之一；仅本次独立 BuildKit 使用受限 runc 包装器写入自己临时 OCI spec，不替换系统 runc。启动时核对真实执行进程的叶节点限额。旧内核不一定能归属全部 buffered writeback，因此不声称绝对磁盘速率保证；继续保留磁盘/内存压力中止门禁。
- 独立 BuildKit 根位于本次输出目录；守护进程使用 `PrivateNetwork=yes` 并检查网络命名空间只有 loopback。全部 RUN 同时使用 `--network=none`。无远程 Dockerfile frontend、无在线安装依赖、无 registry 拉取后备。
- 构建前根分区至少 30 GiB 可用、使用率低于 80%，`/opt` 至少 80 GiB 可用，主机至少 16 GiB 可用内存；运行中保留 4 GiB 可用内存。
- 阶段之间和长阶段中定期检查节点压力、三个实际 Deployment/镜像身份、Gateway 不同节点。变化即中止构建；这是有限采样窗口，不代表绝对零资源影响或独占节点。
- 三个完整源锚点检查全部通过后，才构建候选。最终校验完整 OCI、原底座层前缀和运行时配置均保留。Frontend 不改变 WorkingDir/启动命令。
- 只停止本次创建的唯一 unit，保留所有输出/失败日志；不清理生产镜像和 containerd。不完整清理视为失败，并保留受限 slice 的保护，禁止直接重复启动。

## 输出与下一步

最终 `BUILD-RESULT.json` 才表示三个候选镜像构建及 OCI 检查完成；单独某一阶段 PASS 不等于完成。结果始终记录 `ready_for_rollout=false`、`images_imported=false`。日志每 20 秒报告阶段存活，详细构建日志为 root 私有文件；不要不经检查上传整份日志。

后续仍需真实 ARM64 业务回归、双用户及知识所有者授权、NFS 跨节点只读挂载、普通聊天/PDF/PPT/渠道回归，以及维护停写、一致性备份、升级后新数据与撤权保留的回退演练。任何一项不满足都不能宣布可安全热更。候选启动验收必须使用隔离数据，不能挂载生产可写员工目录。

上线控制还必须把 Gateway 的 `app/gateway/app.py` 单独切换到已审阅候选专用不可变
ConfigMap；保留旧共享调度 ConfigMap、其余 14 个 Gateway 挂载和全部 PPT Runner 挂载。
本包不执行该切换，不提供生产发布脚本，不能仅凭构建成功就手工 `set image`。

实现依据：[BuildKit cgroup-parent](https://github.com/moby/buildkit/blob/v0.24.0/frontend/dockerui/config.go)、[OCI executor cgroups](https://github.com/moby/buildkit/blob/v0.24.0/executor/oci/spec.go)、[systemd resource controls](https://github.com/systemd/systemd/blob/v239/man/systemd.resource-control.xml)。这些文档不替代目标服务器上的内核限额验证。

cgroup v1 边界依据：[Linux blkio hierarchy](https://docs.kernel.org/admin-guide/cgroup-v1/blkio-controller.html)。生产主机不因本构建器被要求修改 cgroup 模式。
