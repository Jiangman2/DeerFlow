# DeerFlow 本地日志过渡部署

独立部署 Alloy（每节点采集）→ Loki（单实例 filesystem）→ Grafana。
目录位于 `k8s/local-pvc/observability`，独立执行 `kubectl apply -k`。
不加入父目录 `k8s/local-pvc/kustomization.yaml` 的 resources，避免父级 namespace 转换和业务发布连带更新日志系统。
不修改业务 Deployment、Nginx 路由、数据库或现有 Helm release。

## 默认值与边界

- 命名空间 `observability`；只采集节点 `/var/log/pods/deer-flow_*/*/*.log`。
- Loki：100Gi local PV；Grafana：5Gi local PV；二者 `Retain`，各一副本，Recreate 更新。
- Loki 保留 720h（30 天），Compactor 延迟删除；不是磁盘容量保证。
- Alloy：每个 Linux 节点一个 Pod，包含有 NoSchedule 污点的节点。若有不允许采集的专用节点，收窄 `alloy.yaml` 的 nodeSelector/tolerations。
- Alloy 以 root 只读访问日志，不使用 privileged，也不申请 Kubernetes API RBAC。元数据由 CRI 与路径解析；`app` 是容器名，不是 Kubernetes app 标签。
- 读取位置和 WAL 保存在每节点 `/var/lib/deerflow-alloy`。WAL 在固定 Alloy 版本中为实验功能，显式设置 experimental；最长段年龄 6h，发送有界重试。它不是无限缓存或绝对不丢日志保证。监控磁盘和 dropped 指标。
- 仅追踪当前 `.log` 文件，正常运行时跟随轮转；不自动导入历史 `.gz` 或时间后缀文件。首次采集读现有文件，Loki 默认拒绝超过 7 天的历史写入。旧日志补录应另行评估时间顺序与重复问题。
- 单节点磁盘故障会影响历史日志。PV 容量声明不会给普通目录设置磁盘配额；应使用容量足够的独立磁盘/分区。文件系统满时 Loki 不能写入。
- Loki/Grafana 均为 ClusterIP，不创建 Ingress/NodePort。Loki 无内置认证；附带 NetworkPolicy 只在 CNI 实际支持策略时生效。纯 Flannel 集群不能把此策略视为访问隔离，集群内非可信租户需要额外认证代理或支持策略的网络方案。
- 镜像固定为 Alloy v1.11.0、Loki 3.6.0、Grafana 12.2.0，部署前按企业镜像扫描策略审核并镜像到内网。版本固定用于可重复部署，不表示已完成线上安全认证。

## 1. 选择存储节点、准备目录

以下是 Linux 运维终端命令。先检查节点磁盘，选择一个有充足空间、长期在线的节点；不要默认把日志放进系统盘。

```bash
kubectl get nodes -o wide
kubectl get nodes -l deerflow-log-storage=true
```

只给一个选定节点标记（将 `NODE_NAME` 替换为实际节点名）：

```bash
kubectl label node NODE_NAME deerflow-log-storage=true
```

在该节点准备目录。若使用新磁盘，先由运维完成挂载，再创建目录：

```bash
df -h /opt
sudo install -d -m 0750 -o 10001 -g 10001 /opt/deerflow-observability/loki
sudo install -d -m 0750 -o 472 -g 472 /opt/deerflow-observability/grafana
```

`storage.yaml` 的两个 PV 通过此标签绑定节点，claimRef 防止被其他 PVC 抢占。
本地 PV 不支持透明跨节点漂移：不要在运行中把标签移到另一节点。
如果修改目录或容量，同步修改 PV 清单和节点实际存储。

### 1.1 实机验证补充（麒麟 V10 ARM64 单节点，2026-09）

**PV 的容量声明不是磁盘配额保证，务必放在数据盘。** Loki 的 PV 声明 100Gi，但 `local` 类型 PV **不会**创建目录、也**不会**施加任何配额——文件系统写满时 Loki 直接写不进去，并连带影响同一文件系统上的其它服务（包括 kubelet 与 etcd）。本次实测节点系统盘 48G / 可用 29G，而数据盘 `/mnt/data` 有 993G，因此改了 PV 路径：

```bash
# 改 storage.yaml 里两个 PV 的 spec.local.path
#   /opt/deerflow-observability/loki    -> /mnt/data/deerflow-observability/loki
#   /opt/deerflow-observability/grafana -> /mnt/data/deerflow-observability/grafana

# local PV 不建目录，必须手工预建并设好属主（Loki=10001，Grafana=472）
sudo install -d -m 0750 -o 10001 -g 10001 /mnt/data/deerflow-observability/loki
sudo install -d -m 0750 -o 472   -g 472   /mnt/data/deerflow-observability/grafana
```

**首次启动会刷一批 400 错误，属预期，不是故障。** Alloy 首次从文件开头读（日志里可见 `Seeked ... Offset:0`），而 Loki 的 `reject_old_samples_max_age: 168h` 会拒掉 7 天前的历史，于是出现 `entry for stream ... has timestamp too old`。这是一次性的（读取位置持久化在 `/var/lib/deerflow-alloy`，读过不会重读），对节点上已运行数周的容器尤其明显。验收时应查询 `{namespace="deer-flow"}` 里**新产生**的日志，而不是纠结这条 400。

### 1.2 实机验证补充：多节点真实业务集群（17 节点 ARM64，2026-09）

在**多节点、有真实业务**的集群上复测时，有三点和单节点不同。

**1）收窄采集范围只能用 `nodeSelector`，用 tolerations 无效。**

**DaemonSet 控制器会无条件给 Pod 追加一批容忍**，其中包含 `node.kubernetes.io/unschedulable:NoSchedule`（还有 `not-ready` / `unreachable` / `disk-pressure` / `memory-pressure` / `pid-pressure`）。因此**已经被 cordon 的节点照样会起 Alloy Pod，无论你写什么 tolerations**——即使只保留自己的专用污点容忍，控制器也会把 `unschedulable` 加回去。现象是 rollout 永远卡在 `N of 18 updated pods available`。

本次 17 个节点里 13 个是 `Ready,SchedulingDisabled`，可调度节点只有 4 个。正确做法是节点白名单：

```bash
for n in datax-01 datax-02 datax-03 ecs-zdz-pro-0022; do
  kubectl label node "$n" deerflow-log-agent=true --overwrite
done
kubectl get nodes -l deerflow-log-agent=true
```

```yaml
# alloy.yaml
spec:
  template:
    spec:
      nodeSelector:
        deerflow-log-agent: "true"
```

**判断是否生效看 `desiredNumberScheduled`，不要看 Pod 是否 Running：**

```bash
kubectl -n observability get ds alloy -o jsonpath='{.status.desiredNumberScheduled}{"\n"}'   # 本次 18 -> 4
```

cordon 节点上的旧 Pod **不会被自动驱逐或删除**，会一直留在那里让 rollout 差几格，需要手工删掉：

```bash
kubectl -n observability get pods -l app=alloy -o wide
kubectl -n observability delete pod <不在白名单节点上的 alloy pod>
```

被 cordon 的节点上若仍有业务 Pod 输出日志，这些日志将不再采集——收窄前先确认这些节点上的业务是否也需要日志。

**2）镜像要逐节点分发。** `imagePullPolicy: IfNotPresent` 按**每个节点本地**的 containerd 缓存判断，不跨节点共享。单节点只导一次即可，多节点上每个 Alloy 目标节点都必须有镜像（无内网仓库时用 `ctr images import` + `ctr images tag` 重打成 `docker.io/grafana/*`，否则照样 `ImagePullBackOff`）。

**3）容量按实测日增量算，不按 PV 声明算。** 本次存储节点数据盘 `/dev/vdb1` 3.0T（可用 2.7T），实测摄取 **244 KB/s ≈ 21 GB/天**，30 天约 **632 GB**，`retention_period: 720h` 可以原样保留。

**WAL 是循环回收的，但采样必须跨 checkpoint 周期，否则会把正常回收误判成"泄漏"。**

| 时刻 | `wal/` 总量 | 段文件 | `checkpoint.*` |
| --- | --- | --- | --- |
| T | 153M | `06`, `07`, `08` | `000005` |
| T+2m | 172M | `06`, `07`, `08`, `09` | `000005` |
| T+5m | **82M** | `09`, `10`（06/07/08 已删） | `000008` + `000009.tmp` |
| T+8m | **53M** | `10` | `000009`（tmp 转正） |

只看前两次采样（153M → 172M）会得出"WAL 单调上涨、无法回收"的错误结论。**至少跨一个 checkpoint 周期（默认 5 分钟）再采样，并同时看 `checkpoint.*` 目录编号是否推进。** 判据是：总量在 50~200M 区间震荡 + 旧段被删 + checkpoint 编号递增 = 健康。

## 2. 内网镜像准备

需在所有 Alloy 目标节点准备 `grafana/alloy:v1.11.0`；在存储节点准备 Loki/Grafana 镜像。
确认镜像包含节点架构（你们的 ARM64 节点需要 linux/arm64）。优先镜像到企业仓库并设置 Kustomize images，私有仓库按企业方式注入 imagePullSecrets。

如果采用离线 containerd 导入，在联网机器按目标架构拉取并打包：

```bash
docker pull --platform linux/arm64 grafana/alloy:v1.11.0
docker pull --platform linux/arm64 grafana/loki:3.6.0
docker pull --platform linux/arm64 grafana/grafana:12.2.0
docker save -o observability-arm64.tar grafana/alloy:v1.11.0 grafana/loki:3.6.0 grafana/grafana:12.2.0
```

传到目标节点后：

```bash
sudo ctr -n k8s.io images import observability-arm64.tar
```

混合架构集群分别准备镜像；不能把仅含 ARM64 的包用于 AMD64 节点。

### 2.1 实机验证补充：Docker Hub 不可达时走镜像源，并且必须重打标签

本次实测环境的企业代理**未放行 `registry-1.docker.io`**（表现为 `curl: (56) Proxy CONNECT aborted`），但放行了 `docker.m.daocloud.io`。关键点是：从镜像源拉下来之后**必须重打成 Docker Hub 规范名**。清单里写的是 `grafana/loki:3.6.0`，会被解析为 `docker.io/grafana/loki:3.6.0`；若本地只有 `docker.m.daocloud.io/...` 这一个名字，`imagePullPolicy: IfNotPresent` 会认为本地没有、转而去连 Docker Hub，然后失败（`ImagePullBackOff`）。

```bash
sudo ctr -n k8s.io images pull --platform linux/arm64 docker.m.daocloud.io/grafana/alloy:v1.11.0
sudo ctr -n k8s.io images pull --platform linux/arm64 docker.m.daocloud.io/grafana/loki:3.6.0
sudo ctr -n k8s.io images pull --platform linux/arm64 docker.m.daocloud.io/grafana/grafana:12.2.0

# 关键：重打标签，使清单里的 grafana/* 能命中本地镜像
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/alloy:v1.11.0   docker.io/grafana/alloy:v1.11.0
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/loki:3.6.0     docker.io/grafana/loki:3.6.0
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/grafana:12.2.0 docker.io/grafana/grafana:12.2.0

sudo ctr -n k8s.io images list | grep -Ei 'alloy|loki|grafana'   # 应同时看到两个名字
```

代理放行清单因企业网络而异；若 `docker.m.daocloud.io` 也不可达，改用企业内网镜像仓库并通过 Kustomize `images` 改写引用（`imagePullSecrets` 按企业方式注入）。

## 3. 渲染、验证、创建密码

在仓库根目录执行。以下 validate 命令只解析配置，不启动服务：

```bash
kubectl kustomize k8s/local-pvc/observability > /tmp/deerflow-observability.yaml
docker run --rm -v "$PWD/k8s/local-pvc/observability:/config:ro" grafana/loki:3.6.0 -config.file=/config/loki-config.yaml -verify-config=true
docker run --rm -v "$PWD/k8s/local-pvc/observability:/config:ro" -e NODE_NAME=validation grafana/alloy:v1.11.0 validate --stability.level=experimental /config/config.alloy
```

镜像无法联网拉取时，在已导入镜像且具有对应容器运行工具的主机验证。containerd 节点可使用配套 nerdctl 的 `--namespace k8s.io run`；不要把 `ctr` 当成 Docker 参数的直接替代。

创建命名空间和 Grafana 密码 Secret。密码交互输入，不写仓库，也不写进命令历史：

```bash
kubectl apply -f k8s/local-pvc/observability/namespace.yaml
read -r -s -p 'Grafana admin password: ' GRAFANA_PASSWORD
printf '\n'
printf '%s' "$GRAFANA_PASSWORD" | kubectl -n observability create secret generic grafana-admin --from-file=password=/dev/stdin
unset GRAFANA_PASSWORD
kubectl apply --dry-run=server -k k8s/local-pvc/observability
kubectl apply -k k8s/local-pvc/observability
kubectl -n observability rollout status deployment/loki --timeout=300s
kubectl -n observability rollout status deployment/grafana --timeout=300s
kubectl -n observability rollout status daemonset/alloy --timeout=300s
kubectl -n observability get pods,pvc -o wide
```

Secret 已存在时不要重复创建。Grafana 首次初始化后，修改该 Secret 不会自动修改数据库中已有管理员密码，需要走 Grafana 的密码管理流程。
首次安装 Loki 就绪前的 Alloy 重试属于预期行为，但必须确认之后恢复发送。

## 4. 查询日志

在自己的管理电脑执行：

```bash
kubectl -n observability port-forward service/grafana 3001:3000
```

访问 `http://127.0.0.1:3001`，用户 `admin`，使用创建 Secret 时设置的密码。
若 kubectl 只能在服务器执行，另用 SSH 本地端口转发；不要把 port-forward 绑定到 0.0.0.0。
Grafana Explore 中选择自动配置的 **DeerFlow Logs**，设置时间范围并查询：

```logql
{namespace="deer-flow", app="gateway"}
{namespace="deer-flow", app="gateway"} |~ "ERROR|Traceback|Timeout"
{namespace="deer-flow", app="nginx"} |~ "upstream|connect\\(\\) failed"
{namespace="deer-flow", pod="gateway-旧Pod名称"}
```

保留 `node`、`pod`、`container`、`stream` 标签和 `pod_uid` 结构化元数据；thread_id/run_id/trace_id 在原文中查询，不新增高基数索引标签。
Gateway 和 PPT Runner Python 堆栈合并上限 256 行/3 秒；其他容器保留逐行日志。

### 4.1 命令行自检（不需要浏览器）

**每条 `port-forward` 之前先清残留。** 旧的 `kubectl port-forward` 进程会继续占着本地端口、并且指向**已被删除的旧 Pod**：新的一次启动后立即退出，而 `curl` 仍然连上那个死目标，表现为"数据查不到"——这是**假阴性**，不是数据丢失。

```bash
pkill -f 'port-forward service/loki'
ps -ef | grep '[p]ort-forward' | wc -l      # 必须为 0，否则后面所有结论都不可信

kubectl -n observability port-forward service/loki 3100:3100 >/tmp/loki-pf.log 2>&1 &
sleep 4
curl -s --noproxy '*' http://127.0.0.1:3100/ready; echo
curl -s --noproxy '*' -G http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="deer-flow"}' --data-urlencode 'limit=2' | head -c 800; echo
```

Grafana 侧（`/api/health` 不需要认证，`/api/datasources` 需要）：

```bash
kubectl -n observability port-forward service/grafana 3000:3000 >/tmp/gf-pf.log 2>&1 &
sleep 4
GPW=$(kubectl -n observability get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d)
echo "pw_len=${#GPW}"                                            # 先确认不是空串
curl -s http://127.0.0.1:3000/api/health; echo
curl -s -u "admin:$GPW" http://127.0.0.1:3000/api/datasources; echo
```

**Secret 的 key 是 `password`，不是 `admin-password`。** 取错 key 时 `-o jsonpath='{.data.admin-password}'` 返回**空串**，`base64 -d` 得到空密码，Grafana 返回 `401 {"message":"Invalid username or password","messageId":"password-auth.failed"}` —— 这不是密码错、更不是 Grafana 故障，而是**取值取错了**。先用下面这条确认 key 名：

```bash
kubectl -n observability get secret grafana-admin \
  -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
```

**Grafana 数据源的 uid 是 `deerflow-loki`（不是 `loki`）。** 用 Grafana 的 `/api/ds/query` 做端到端校验时要用清单里 `datasources.yaml` 的真实 uid：

```bash
GPW=$(kubectl -n observability get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d)
curl -s -u "admin:$GPW" -H 'Content-Type: application/json' \
  http://127.0.0.1:3000/api/ds/query \
  -d '{"queries":[{"refId":"A","datasource":{"type":"loki","uid":"deerflow-loki"},"expr":"{namespace=\"deer-flow\"}","maxLines":3}],"from":"now-15m","to":"now"}'
```

`/api/datasources` 应返回 `{"uid":"deerflow-loki","name":"DeerFlow Logs","type":"loki","url":"http://loki:3100","isDefault":true}`。

## 5. 必须完成的上线验收

不要删除业务 Gateway 做测试。使用预先导入的现有轻量镜像创建临时 Pod（替换 IMAGE）：

```bash
kubectl -n deer-flow run log-retention-smoke --image=IMAGE --restart=Never -- sh -c 'echo deerflow-retention-smoke-001; sleep 120'
```

1. 在 Grafana 查 `{namespace="deer-flow"} |= "deerflow-retention-smoke-001"`，确认找到并记下时间。
2. 删除测试 Pod：`kubectl -n deer-flow delete pod log-retention-smoke`，同一查询应仍能查到记录。
3. 执行 `kubectl -n observability rollout restart daemonset/alloy`，确认恢复采集且已有日志仍可查。
4. 执行 `kubectl -n observability rollout restart deployment/loki`，等待就绪，确认历史仍可查询；原始日志、WAL、索引与 Compactor 标记均在同一持久卷。
5. 维护窗口内将 Loki 临时缩为 0，创建另一个持续输出日志的测试 Pod，短暂等待后恢复 Loki 为 1；确认补发且无持续 dropped 错误。此测试暂停日志查询，不停止业务服务。

清理仅限测试 Pod，不删除日志 PVC/PV。

## 6. 运维、升级与退出

```bash
kubectl -n observability logs -l app=alloy --all-containers=true --since=15m --tail=-1 --prefix=true
kubectl -n observability logs deployment/loki --since=15m
kubectl -n observability get pods,pvc -o wide
```

节点检查：`df -h /opt/deerflow-observability`、`sudo du -sh /var/lib/deerflow-alloy`。

**`sudo` 不会替你展开通配符 —— `*` 是调用者（普通用户）的 shell 展开的。**

Loki 数据目录属主是 `10001`、权限 `drwxrws---`，普通用户无权列出；`/var/log/pods` 下的 Pod 目录同理。此时 glob 展开失败，**字面量**被原样传给 `sudo`，报出误导性的"目录不存在"：

```bash
sudo du -sh /opt/deerflow-observability/loki/*          # ✗ No such file or directory（目录其实存在，只是没权限列出）
sudo sh -c 'du -sh /opt/deerflow-observability/loki/*'  # ✓ 交给 root 的 shell 展开
sudo sh -c 'ls -la /var/log/pods/observability_loki-*/loki/'   # ✓ 同理
```

**Loki 自身 stdout 不要只看 `kubectl logs`。** 容器日志文件是 `/var/log/pods/observability_loki-*/loki/0.log`，kubelet 默认按 10Mi × 5 轮转，`kubectl logs` 只返回**当前文件**的内容，更早的部分可能已切到 `.log.1` 或不在当前文件里。要确认启动、WAL 回放等启动阶段行为，直接读文件：

```bash
sudo sh -c 'ls -la /var/log/pods/observability_loki-*/loki/'          # 先看有没有 .log.1、当前文件多大
sudo sh -c 'wc -l /var/log/pods/observability_loki-*/loki/0.log; head -5 /var/log/pods/observability_loki-*/loki/0.log'
```

实测参考值：Loki 自身输出约 **20 行/分钟**（350 行 / 85 KB 覆盖 17 分钟），单文件、无 `.log.1`，**根本不会触发 10Mi 轮转**；`0.log` 第 1 行就是 `msg="Starting Loki" version="(version=3.6.0..."`。**所以"`kubectl logs` 里看不到启动行"不等于"日志被轮转掉了"**——先量文件大小和行数再下结论（本次就误判过一次，成本是白查了半小时）。

Alloy `/metrics` 暴露 `loki_write_dropped_entries_total`、`loki_write_batch_retries_total` 等指标；本套过渡清单不部署 Prometheus/告警平台，必须由运维检查或接入现有监控，不能把 Pod Ready 当作日志已成功送达。
30 天保留不按剩余磁盘自动缩短；建议磁盘达到 70% 时处理容量，85% 时紧急处理。按实测每日新增量估算 30 天容量并留出 WAL/索引余量。

配置修改后重新 `kubectl apply -k k8s/local-pvc/observability`，带哈希的 ConfigMap 引用会触发更新。旧 ConfigMap 可在确认无人引用后单独清理。
临时停止采集可删除 Alloy DaemonSet，停止 Loki/Grafana 可缩为 0；不要执行整个目录的 `kubectl delete -k` 或删除命名空间来“重启”。
`Retain` 保护 PV 数据，但误删 PVC 后需要管理员重新绑定，不能期待自动恢复。
本地盘故障无法自动恢复：定期在停写/停服务窗口做一致性备份，并将备份放到另一故障域。不要仅复制运行中的 Grafana SQLite 文件作为可靠备份。

迁移 S3 时保留 filesystem 的旧 schema 时间段，为未来日期新增 S3 schema，并验证旧时间段仍可查询；不能只改存储类型后删除旧 PV。实际迁移单独实施。

**完整部署与验收步骤**（含环境侦察、镜像获取、`tail_from_end`、五项上线验收、Grafana 访问）见
[`docs/observability-logging-stack-deployment.md`](../../../docs/observability-logging-stack-deployment.md)。

官方参考：
- https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/
- https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/
- https://grafana.com/docs/loki/latest/operations/storage/retention/
- https://grafana.com/docs/loki/latest/operations/storage/filesystem/
