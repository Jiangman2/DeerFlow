# K8s 可观测性栈部署手册（Alloy + Loki + Grafana）

> 面向**已有业务在跑**的 Kubernetes 集群，部署 `k8s/local-pvc/observability` 这套日志栈。
> 本手册来自实机部署与验收，命令与数字均为实测，不是推导：
> - §1–§11 来自**单节点**集群（麒麟 V10 ARM64，节点 `dmdb`，控制面与业务同机）；
> - **§12 来自 17 节点 ARM64 真实业务集群（`datax`）**，补充多节点放置、镜像分发、日志量归因与新增操作坑。

## 1. 组件与定位

| 组件 | 版本 | 作用 | 关键约束 |
| --- | --- | --- | --- |
| **Alloy** | v1.11.0 | 每节点采集容器日志 | DaemonSet，root 只读挂 `/var/log/pods`，**无 Kubernetes API 权限**（`automountServiceAccountToken: false`） |
| **Loki** | 3.6.0 | 日志存储与索引 | 单副本，文件系统存储，保留 720h（30 天） |
| **Grafana** | 12.2.0 | 查询界面 | 单副本，数据源自动配置 |

**定位：过渡方案，不是生产 HA 日志平台。**

- 单副本 Loki、文件系统存储：节点本地盘故障即丢历史
- **Loki 完全无认证**（`auth_enabled: false`）
- 附带的 `NetworkPolicy` 在**纯 Flannel 集群中不生效**（Flannel 不实现 NetworkPolicy，且本集群无策略控制器）——集群内任意 Pod 都能直连 `loki:3100` 读到全部日志
- 不部署 Prometheus/告警平台；**Pod Ready ≠ 日志已送达**，必须人工核验（见 §7）

## 2. 部署前必须确认的三件事

### 2.1 采集范围——最可能让部署白做的一条

`config.alloy` 中路径是**写死的**：

```alloy
path_targets = [{
  "__path__" = "/var/log/pods/deer-flow_*/*/*.log",
  "namespace" = "deer-flow",
}]
```

**只采集 `deer-flow` 命名空间。** 如果业务跑在别的命名空间，照原样部署完会发现 Grafana 里一条日志都没有——不是故障，是根本没采。部署前必须先确认业务所在的命名空间集合，必要时同步修改 `path_targets`、`namespace` 标签以及 `loki.process` 里的 filename 正则。

### 2.2 容量与磁盘隔离

Loki 声明 100Gi、保留 30 天。两个必须记住的事实：

- **`local` 类型 PV 的容量是"声明"，不是配额。** 它不会创建目录、不会施加任何限制。文件系统写满时 Loki 直接写失败，并连带影响**同一文件系统上的其它服务**（包括 kubelet 与 etcd）。
- 因此**必须放在与业务隔离的盘上**，并按实测日增量估 30 天容量，留出 WAL 与索引余量。

本次实测节点：`/`（系统盘）444G / 可用 215G；**`/opt` 是 30T 数据盘 / 可用 28T**，因此沿用清单默认路径 `/opt/deerflow-observability/`。
（另一台机器上系统盘仅 29G 可用，就必须把 PV 路径改到数据盘，否则 100Gi 会写爆系统盘。）

### 2.3 安全边界

- **不要对 `service/loki` 做任何端口暴露**，它无认证，等于泄露全部业务日志
- Grafana 有登录认证，可有限暴露（见 §10）
- 集群内非可信租户场景需要额外认证代理或支持策略的网络方案

## 3. 环境侦察（全部只读）

**部署前先跑一遍，确认没有冲突、没有阻塞项。**

```sh
echo "===== 1. 主机与集群 ====="
uname -m; cat /etc/os-release | head -3; hostname
kubectl version
kubectl get nodes -o wide

echo "===== 2. 命名空间与业务负载 ====="
kubectl get ns
kubectl get pods -A -o wide | head -40

echo "===== 3. 是否已有日志/监控栈（避免重复部署）====="
kubectl get pods -A 2>/dev/null \
  | grep -iE 'loki|alloy|promtail|fluent|vector|filebeat|elastic|grafana|prometheus|victoria' \
  || echo "(未发现既有日志栈)"

echo "===== 4. 存储能力 ====="
kubectl get storageclass
kubectl get pv

echo "===== 5. 磁盘布局（决定 Loki 放哪）====="
df -h
lsblk 2>/dev/null | head -25

echo "===== 6. firewalld / Pod 网络健康度 ====="
systemctl is-active firewalld
systemctl is-enabled firewalld
echo -n "FORWARD 中 icmp-host-prohibited 条数: "
sudo iptables -S FORWARD 2>/dev/null | grep -c icmp-host-prohibited
kubectl -n kube-system get pods -o wide | grep -E 'coredns|kube-proxy'
kubectl -n kube-system get endpoints kube-dns

echo "===== 7. 采集目标与日志量 ====="
kubectl get ns deer-flow 2>/dev/null || echo "(没有 deer-flow 命名空间 —— 默认采集范围采不到东西)"
sudo du -sh /var/log/pods

echo "===== 8. 运行时与镜像 ====="
sudo ctr -n k8s.io images list 2>/dev/null | grep -Ei 'alloy|loki|grafana' || echo "(本机无这三个镜像)"
```

判读表：

| 检查项 | 期望 | 不符合时的动作 |
| --- | --- | --- |
| `coredns` 是否 `1/1` | 全 `1/1` | 若 `0/1` 且 `kube-dns` endpoints 为空 → **集群 DNS 已废**，先修（常见根因：firewalld 的 `FORWARD`/`INPUT` 兜底 REJECT 排在 CNI ACCEPT 规则之前） |
| `firewalld` | `inactive` **且 `disabled`** | 仅 `inactive` 不够——`enabled` 会在重启后复活并掐断 Pod 网络 |
| 默认 StorageClass | 本栈**不需要** | 自己创建 `deerflow-logs-local`（`no-provisioner` + 静态 PV） |
| 既有日志栈 | 无 | 有则先评估是否重复，避免两套采集互相干扰 |
| `/var/log/pods` 体积 | 参考日增量 | 决定 Loki 容量与是否需要保留策略调整 |

## 4. 准备

### 4.1 传输清单

栈目录是自包含的（`kustomization.yaml` 只引用同目录文件），单独传这一个目录即可：

```powershell
# 工作机
scp -r "<repo>\k8s\local-pvc\observability" cnooc@<目标机>:/home/cnooc/
```

或从指定分支导出（避免切换工作区分支）：

```bash
git archive --format=tar -o observability.tar <branch> k8s/local-pvc/observability
# 目标机
mkdir -p ~/obs-manifests && tar -xf /tmp/observability.tar -C ~/obs-manifests
ls -1 ~/obs-manifests/k8s/local-pvc/observability/   # 应为 11 个文件
```

### 4.2 数据目录（属主必须匹配容器用户）

`local` PV **不会创建目录**，也不像 hostPath 那样兜底，所以必须预建且属主正确：

```sh
sudo install -d -m 0750 -o 10001 -g 10001 /opt/deerflow-observability/loki
sudo install -d -m 0750 -o 472   -g 472   /opt/deerflow-observability/grafana
ls -ld /opt/deerflow-observability/loki /opt/deerflow-observability/grafana
```

> Loki 容器以 `10001:10001` 运行，Grafana 以 `472:472` 运行。属主不对 → `CrashLoopBackOff` + `permission denied`。
> 若沿用集群既有的存储根目录惯例（例如实测集群用 `/opt/deerflow-storage/<用途>`），应**同时修改 `storage.yaml` 与 README 并提交**，不要只在一台机器上悄悄分叉。

### 4.3 节点标签（PV 靠它绑定）

```sh
kubectl label node <NODE_NAME> deerflow-log-storage=true --overwrite
kubectl get nodes -l deerflow-log-storage=true
```

> `storage.yaml` 的两个 PV 通过该标签做 `nodeAffinity`，并用 `claimRef` 防止被其它 PVC 抢占。**不要在运行中把标签移到另一节点**——本地 PV 不支持透明漂移。

### 4.4 镜像获取（本节含一个高频陷阱）

需三个镜像：`grafana/alloy:v1.11.0`、`grafana/loki:3.6.0`、`grafana/grafana:12.2.0`，架构必须是 `linux/arm64`。

**先探测哪个源可达**（不要盲试）：

```sh
for img in "grafana/alloy:1.11.0" "grafana/loki:3.6.0" "grafana/grafana:12.2.0"; do
  repo="${img%%:*}"; tag="${img##*:}"
  code=$(curl -fkSL -m 20 -o /dev/null -w '%{http_code}' \
    -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json' \
    "https://docker.m.daocloud.io/v2/${repo}/manifests/${tag}")
  echo "daocloud  ${img}  -> HTTP ${code}"
done
```

`401` 表示需要 token 鉴权但**源是通的**（`curl` 匿名拿不到 token，`ctr` 会自动处理）；`000`/超时表示不可达。

#### ⚠️ 陷阱：`sudo` 会清掉代理，而 `ctr` 的下载在**客户端进程**内完成

这是本次最耗时的一个坑，必须理解清楚：

| 拉取路径 | HTTP 由谁发起 | 读谁的环境变量 |
| --- | --- | --- |
| **`ctr images pull`** | **`ctr` 进程（客户端 resolver）** | **`ctr` 进程的环境** |
| kubelet / CRI 拉取 | containerd daemon | daemon 的环境 |

所以 **"containerd 单元里有代理" ≠ "`ctr pull` 有代理"**。`sudo` 默认 `env_reset`，会清掉 `http_proxy`，导致 `ctr` 直连公网 IP 然后被 reset：

```
read tcp 192.168.110.14:54208->101.132.143.33:443: read: connection reset by peer
```

注意 `->101.132.143.33:443` 是目标真实 IP——**若走了代理，peer 应该是代理地址**。看到直连 IP 就说明代理没生效。

**做法：把代理写进 root 专用文件（避免密码进 argv 与 shell 历史），拉取时显式 source。**

```sh
# 从 containerd 单元已有的配置里取，不用手打密码
sudo install -m 600 /dev/null /etc/deerflow-proxy.env
sudo sh -c 'systemctl show containerd -p Environment --value \
  | tr " " "\n" \
  | grep -E "^(HTTP_PROXY|HTTPS_PROXY|NO_PROXY)=" > /etc/deerflow-proxy.env'
sudo chmod 600 /etc/deerflow-proxy.env
sudo sed 's/:[^:@]*@/:***@/' /etc/deerflow-proxy.env   # 核对（密码已打码）

# 烟测（几秒出结果，别一上来就拉 180MB）
sudo sh -c 'set -a; . /etc/deerflow-proxy.env; set +a; \
  ctr -n k8s.io images pull --platform linux/arm64 docker.m.daocloud.io/library/hello-world:latest'
sudo ctr -n k8s.io images rm docker.m.daocloud.io/library/hello-world:latest

# 正式拉取（三个镜像合计约 350MB，按 1MB/s 约 6 分钟）
for img in \
  docker.m.daocloud.io/grafana/alloy:v1.11.0 \
  docker.m.daocloud.io/grafana/loki:3.6.0 \
  docker.m.daocloud.io/grafana/grafana:12.2.0 ; do
  echo "=== pull $img ==="
  sudo sh -c "set -a; . /etc/deerflow-proxy.env; set +a; \
    ctr -n k8s.io images pull --platform linux/arm64 $img" 2>&1 | tail -3
done
```

#### 必须重打成 Docker Hub 规范名

清单里写的是 `grafana/loki:3.6.0`，会被解析为 `docker.io/grafana/loki:3.6.0`。若本地只有 `docker.m.daocloud.io/...`，`imagePullPolicy: IfNotPresent` 会认为本地没有，转而去连（可能被墙的）Docker Hub → `ImagePullBackOff`。

```sh
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/alloy:v1.11.0   docker.io/grafana/alloy:v1.11.0
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/loki:3.6.0     docker.io/grafana/loki:3.6.0
sudo ctr -n k8s.io images tag docker.m.daocloud.io/grafana/grafana:12.2.0 docker.io/grafana/grafana:12.2.0

# 应同时看到两个名字，且都含 linux/arm64
sudo ctr -n k8s.io images list | grep -Ei 'alloy|loki|grafana'
```

## 5. 渲染审查 → dry-run → apply

### 5.1 渲染审查（不落盘、不改集群）

```sh
cd ~/obs-manifests/k8s/local-pvc/observability
kubectl kustomize . > /tmp/obs-rendered.yaml
echo "渲染行数: $(wc -l < /tmp/obs-rendered.yaml)"

grep -nE '^kind:|^  name:' /tmp/obs-rendered.yaml | head -40      # 对象清单
grep -nE 'image: ' /tmp/obs-rendered.yaml                          # 镜像名
grep -nE 'path: /opt|deerflow-log-storage|storageClassName|claimName|storage: ' /tmp/obs-rendered.yaml
grep -nE 'deer-flow_|"namespace"' /tmp/obs-rendered.yaml | head    # 采集范围
```

应看到 15 个对象：Namespace / StorageClass / ConfigMap×3 / Service×2 / PV×2 / PVC×2 / Deployment×2 / DaemonSet / NetworkPolicy。

### 5.2 顺序很重要：先建命名空间，再 dry-run

**`kubectl apply --dry-run=server` 会把对象拿到真集群校验，因此带 `namespace` 的对象要求该命名空间已存在。** kustomize 里虽然含 `namespace.yaml`，但 dry-run 不落盘、不会先创建它，于是所有带命名空间的对象全部报：

```
Error from server (NotFound): error when creating ".": namespaces "observability" not found   ×10
```

**这是顺序问题，不是清单缺陷。** 正确顺序：

```sh
# 1) 命名空间
kubectl apply -f namespace.yaml
kubectl get ns observability

# 2) Grafana 密码 Secret（密码交互输入，不写仓库、不进命令历史）
read -r -s -p 'Grafana admin password: ' GRAFANA_PASSWORD; printf '\n'
printf '%s' "$GRAFANA_PASSWORD" | kubectl -n observability create secret generic grafana-admin --from-file=password=/dev/stdin
unset GRAFANA_PASSWORD
kubectl -n observability get secret grafana-admin

# 3) dry-run（此时应全部 created，无 Error）
kubectl apply --dry-run=server -k . 2>&1 | tail -20
```

> Secret 已存在时**不要重复创建**。Grafana 首次初始化后再改该 Secret **不会**改掉已有管理员密码，需走 Grafana 的密码管理流程。

### 5.3 正式 apply

```sh
kubectl apply -k . 2>&1 | tail -20
```

## 6. 就绪验证

```sh
kubectl -n observability rollout status deployment/loki    --timeout=300s
kubectl -n observability rollout status deployment/grafana --timeout=300s
kubectl -n observability rollout status daemonset/alloy    --timeout=300s

kubectl -n observability get pods,pvc -o wide
kubectl get pv | grep -E 'NAME|deerflow-logs'
sudo du -sh /opt/deerflow-observability/loki /opt/deerflow-observability/grafana
```

| 检查 | 期望 |
| --- | --- |
| 三个 rollout | 全部 `successfully rolled out` |
| Pod | alloy / loki / grafana 全 `1/1 Running` |
| PVC | `loki-data`、`grafana-data` 均 **Bound** |
| PV | `deerflow-logs-{loki,grafana}` 均 Bound 到对应 claim |
| 落盘 | 两个目录出现 `chunks/`、`wal/`、`tsdb-index/` 等，几 MB 量级 |

### 业务零影响核对（有业务的集群必做）

**在 apply 前记录基线，apply 后逐项对比：**

```sh
# apply 前
kubectl -n deer-flow get pods --no-headers | wc -l
kubectl -n deer-flow get pods --no-headers | awk '{print $3}' | sort | uniq -c

# apply 后（应逐项一致）
kubectl -n deer-flow get pods --no-headers | wc -l
kubectl -n deer-flow get pods --no-headers | awk '{print $3}' | sort | uniq -c
kubectl -n deer-flow get pods --no-headers | grep -vE 'Running|Completed' || echo "(无异常)"
```

> 实测基线：`26 = 17 Running / 7 Completed / 1 ErrImageNeverPull / 1 Pending`；验收测试 Pod 会临时增加 `Completed` 计数，清掉后应回到基线。

## 7. "日志真的送达"验证（Pod Ready ≠ 送达）

**这是本手册最重要的一节。** Alloy 的 Pod 可以 `1/1 Running` 而**一条日志都没送出去**——例如 Loki 尚未开始监听时：

```
level=warn msg="error sending batch, will retry" ... dial tcp <ClusterIP>:3100: connect: connection refused
```

README 原文：**"不能把 Pod Ready 当作日志已成功送达"**。判据必须来自 Loki 侧与 Alloy 指标。

### 7.1 从 Loki API 确认（决定性）

```sh
kubectl -n observability port-forward service/loki 3100:3100 >/tmp/loki-pf.log 2>&1 &
PF=$!; sleep 4

echo "--- 已建立的 label ---"
curl -s --noproxy '*' "http://127.0.0.1:3100/loki/api/v1/labels"; echo

echo "--- stream 列表（应出现真实业务容器）---"
curl -s --noproxy '*' -G "http://127.0.0.1:3100/loki/api/v1/series" \
  --data-urlencode 'match[]={namespace="deer-flow"}' | head -c 1500; echo

echo "--- 取几条业务日志 ---"
curl -s --noproxy '*' -G "http://127.0.0.1:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={namespace="deer-flow", container="gateway"}' \
  --data-urlencode 'limit=3' | head -c 1200; echo

kill $PF 2>/dev/null
```

期望 labels 含 `app/container/namespace/node/pod/service_name/stream`，`series` 含真实业务容器名。

### 7.2 从 Alloy 指标确认（运维侧权威）

```sh
APOD=$(kubectl -n observability get pods -l app=alloy -o jsonpath='{.items[0].metadata.name}')
kubectl -n observability port-forward "pod/$APOD" 12345:12345 >/tmp/alloy-pf.log 2>&1 &
PF=$!; sleep 4
curl -s --noproxy '*' http://127.0.0.1:12345/metrics | grep -E '^loki_(write|source_file)' | grep -vE '^#'
kill $PF 2>/dev/null
```

关键指标：

| 指标 | 含义 |
| --- | --- |
| `loki_source_file_read_bytes_total` | 每个文件已读字节（与 `file_bytes_total` 对比可知追赶进度） |
| `loki_write_dropped_entries_total{reason=...}` | 被丢弃条目数。`ingester_error` = Loki 拒绝；`rate_limited`/`stream_limited` = 限流 |
| `loki_write_batch_retries_total` | 重试次数 |
| `loki_write_request_duration_seconds_count{status_code=...}` | 各状态码请求数（**全是 400/-1、没有 2xx 就说明一直没成功**） |

## 8. 首装历史回灌：现象、机理与 `tail_from_end`

### 8.1 现象

首次启动时 Alloy 会**从文件头读**（日志里是 `Seeked ... &{Offset:0 Whence:0}`）。若节点上的容器已运行数天/数周，会把这些历史条目推给 Loki，然后被拒绝：

```
entry too far behind, entry timestamp: ...                    ← ingester 发送窗口（比 7 天更严，约 2 小时量级）
entry for stream '{...}' has timestamp too old: ...           ← limits_config.reject_old_samples_max_age = 168h
```

实测后果（一台有 75 天历史容器的节点）：

```
loki_write_dropped_entries_total{reason="ingester_error"} = 667305
loki_write_request_duration_seconds_count{status_code="400"} = 608
loki_write_request_duration_seconds_count{status_code="-1"}  = 5      ← 无任何 2xx
WAL: 38M → 86M → 118M → 200M（持续增长）
```

**收益为零**——这些历史 100% 会被拒——却在业务机上持续消耗 CPU/IO/磁盘。按 `read_bytes` 与 `file_bytes` 对比，`postgres/0.log`（161MB）当时只读了 19.5MB，照此速度要很久才能追完。

### 8.2 一个重要的认知纠正

**Loki 返回 400 时仍会写入其中的合法条目**（只丢弃超龄的），即"部分成功"。证据：上述 613 次 push 全部失败，但 Loki 里确实有数据、业务日志可查、测试 Pod 的日志也查得到。

所以这类拒绝**不会**把同批次里的新日志"连坐"丢弃。真正的代价是**无谓的资源消耗**，而不是数据丢失。

### 8.3 处置：`tail_from_end = true`

`loki.source.file` 支持该参数（[官方文档](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/)）：

> `tail_from_end` (bool) — Whether to tail from end **if a stored position isn't found**. Default `false`.
> When set to true, only new logs are read, ignoring the existing ones.

在 `config.alloy` 中：

```alloy
loki.source.file "pods" {
  targets = local.file_match.pods.targets
  forward_to = [loki.process.pods.receiver]
  // On a first deployment there is no stored position, so tail from the end and
  // read only new logs. Otherwise the tailer walks days or weeks of history that
  // Loki rejects anyway (ingester window: "entry too far behind"; retention:
  // reject_old_samples_max_age). Because Loki validates a whole push, new entries
  // batched together with old ones are discarded too.
  // Trade-off: a Pod that appears later can lose the seconds of output written
  // before Alloy discovers its file.
  tail_from_end = true
}
```

**生效范围**：只在**没有读取位置记录**时生效。位置记录持久化在 `/var/lib/deerflow-alloy`，因此：

- 该机器**以后重新 apply 仓库版本也不会重演追赶**（位置记录已存在）
- **只有在别的节点首次部署时才会重演**

**取舍（务必知情）**：新出现的 Pod 可能丢掉被发现前的那几秒日志（`sync_period` 5s 窗口）。对崩溃重启的排查，首行往往最关键。若更看重"新 Pod 首行不丢"，就保留默认 `false`，接受一次性追赶。

### 8.4 清掉已有的无用积压

若已经跑了一段无用追赶，这样收尾：

```sh
kubectl -n observability delete daemonset alloy        # 停采集（我们自己的组件）
sleep 5
kubectl -n observability get pods -l app=alloy
sudo rm -rf /var/lib/deerflow-alloy/*                   # 清 WAL 与读取位置
kubectl apply -k .                                      # 带新配置重新部署
kubectl -n observability rollout status daemonset/alloy --timeout=180s
```

实测效果：

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| WAL | 200M（仍涨） | **336K** |
| 近 3 分钟重试 / 终弃 | 10 / 10 | **0 / 0** |
| 读取位置 | `Offset:0` | **`Offset:<文件末尾>`** |

## 9. 五项上线验收

**必须全部做完。** 用一个轻量镜像跑临时 Pod 造标记日志（不要用业务 Pod 做测试）：

```sh
loki_check() {
  marker="$1"
  kubectl -n observability port-forward service/loki 3100:3100 >/tmp/loki-pf.log 2>&1 &
  PF=$!; sleep 4
  resp=$(curl -s --noproxy '*' -G "http://127.0.0.1:3100/loki/api/v1/query_range" \
    --data-urlencode "query={namespace=\"deer-flow\"} |= \"$marker\"" \
    --data-urlencode 'limit=200')
  kill $PF 2>/dev/null
  cnt=$(printf '%s' "$resp" | grep -o "$marker" | wc -l)
  if [ "$cnt" -gt 0 ]; then echo "  OK   [$marker] 命中 $cnt 条"; else echo "  FAIL [$marker] 查不到"; fi
}

IMG=$(sudo ctr -n k8s.io images list --quiet | grep -i busybox | head -1)   # 选一个本地已有轻量镜像
```

| # | 验收项 | 做法 | 期望 |
| --- | --- | --- | --- |
| 1 | 能采集到 | 起一个输出标记日志的临时 Pod，查该标记 | 查得到 |
| 2 | **删 Pod 后日志仍在**（核心价值） | 删掉该 Pod，再查同一标记 | 仍查得到 |
| 3 | 重启 Alloy 后恢复采集且历史留存 | `rollout restart daemonset/alloy`，新造一条 + 查老标记 | 新的查得到、老的仍在 |
| 4 | 重启 Loki 后历史仍可查 | `rollout restart deployment/loki`，再查老标记 | 仍查得到（PV 持久化生效） |
| 5 | Loki 缩 0 期间日志能补发 | `scale deployment/loki --replicas=0` → 造日志 → `--replicas=1` → 等待 → 查 | 离线期间的日志**全部补发**，`dropped`/`final error` = 0 |

第 5 项参考脚本：

```sh
kubectl -n observability scale deployment/loki --replicas=0
sleep 15
# 造日志（离线期间）
kubectl -n deer-flow run smoke-gap --image="$IMG" --image-pull-policy=Never --restart=Never \
  --command -- /bin/sh -c 'for i in $(seq 1 30); do echo "deerflow-retention-smoke-gap gap=$i"; sleep 3; done'
sleep 45
sudo du -sh /var/lib/deerflow-alloy          # WAL 应增长（本地缓冲）
kubectl -n observability logs -l app=alloy --tail=40 \
  | grep -oE 'error sending batch|connection refused|final error|dropped' | sort | uniq -c
                                             # 出现 connection refused 是【正确的重试行为】
kubectl -n observability scale deployment/loki --replicas=1
kubectl -n observability rollout status deployment/loki --timeout=300s
sleep 60
loki_check deerflow-retention-smoke-gap      # 应补发成功
kubectl -n observability logs -l app=alloy --since=5m | grep -ciE 'dropped|final error'   # 应为 0
```

**验收后清理，只删测试 Pod，绝不动 PVC/PV**：

```sh
kubectl -n deer-flow delete pod smoke-gap smoke-002 smoke-003 smoke-004 --ignore-not-found
kubectl -n deer-flow get pods --no-headers | awk '{print $3}' | sort | uniq -c   # 应回到基线
```

## 10. Grafana 访问

> **现状（2026-09）：集群内这套 Grafana 的 UI 暂不需要开放。**
> datax 与 dmdb 的日志目前都通过**集群外那台统一 Grafana**消费（`kubectl port-forward` 方案，见
> [observability-external-grafana-loki-access.md](./observability-external-grafana-loki-access.md)），
> 集群内的 Grafana 只作为回退手段保留。本节两小节的做法**仍然有效**，需要在集群内直接看 Grafana 时再按它操作。

### 10.1 标准方式（SSH 本地端口转发）

```bash
# 目标机
kubectl -n observability port-forward service/grafana 3001:3000
```
```powershell
# 工作机
ssh -L 3001:127.0.0.1:3001 cnooc@<目标机> -N
```
浏览器 `http://127.0.0.1:3001`，用户 `admin`。

> **不要把 port-forward 绑定到 `0.0.0.0`**（README 明确要求）。

### 10.2 若 SSH 报 `administratively prohibited`

先看 sshd 的真实策略：

```sh
sudo sshd -T | grep -iE 'allowtcpforwarding|permitopen|gatewayports'
```

典型受限配置：

```
allowtcpforwarding yes
permitopen 127.0.0.1:80          ← 只允许转发到这一个目标
```

这**不是禁用转发**，而是**目标白名单**。此时把转发目标改成白名单里的地址即可（本地端口随意）：

```bash
# 目标机：把 Grafana 绑到回环的 80（特权端口需要 root）
sudo sh -c 'KUBECONFIG=/home/cnooc/.kube/config kubectl -n observability port-forward --address 127.0.0.1 service/grafana 80:3000'
```
```powershell
# 工作机：目标改 127.0.0.1:80
ssh -L 3001:127.0.0.1:80 cnooc@<目标机> -N
```

浏览器仍访问 `http://127.0.0.1:3001`。

**这条路比绑网卡更安全**：监听只在回环，暴露面由 sshd 的白名单兜住。

用完两个终端都 Ctrl-C，并确认无残留监听：

```sh
sudo ss -tlnp | awk '$4 ~ /:80$/ {print}' || echo "(已无监听)"
```

### 10.3 长期方案（需运维决策）

- 让运维给账号放行转发（最小改动）：
  ```
  Match User <user>
      AllowTcpForwarding local
  ```
- 或把 Grafana 接入企业统一入口（Ingress / 堡垒机 Web 代理）。本集群当前无 Ingress Controller，需评估。

### 10.4 常用 LogQL

```logql
{namespace="deer-flow"}
{namespace="deer-flow"} |~ "ERROR|Traceback|Timeout"
{namespace="deer-flow", container="gateway"} |~ "upstream|connect\\(\\) failed"
{namespace="deer-flow", pod="gateway-8555cf5978-s92wk"}
```

> `tail_from_end` 生效后，**部署时刻之前的日志不在 Loki 里**（有意跳过）。要查更早的只能看容器本地 `/var/log/pods`。

## 11. 运维要点与已知边界

| 项 | 说明 |
| --- | --- |
| **`ctr` 拉镜像** | 必须显式带代理：`sudo sh -c 'set -a; . /etc/deerflow-proxy.env; set +a; ctr -n k8s.io images pull <ref>'`。见 §4.4 |
| **Alloy WAL 位置** | 宿主 `/var/lib/deerflow-alloy`（在系统盘）。受 6h 段龄约束、量很小（稳态几百 KB~几 MB），一般无需迁移；异常增长时迁移并调整 `alloy.yaml` 的 hostPath |
| **30 天保留 ≠ 容量保证** | `retention_period: 720h` 不按剩余磁盘自动缩短。建议磁盘 70% 时处理容量、85% 时紧急处理 |
| **`Retain` 的保护范围** | 保护 PV 数据，但**误删 PVC 后需管理员重新绑定**，不能期待自动恢复 |
| **本地盘故障** | 无法自动恢复。需定期在停写窗口做一致性备份，并放到另一故障域。**不要仅复制运行中的 Grafana SQLite 文件**当作可靠备份 |
| **NetworkPolicy** | 纯 Flannel 下不生效，不要当作访问隔离 |
| **配置更新** | 改配置后重新 `kubectl apply -k`；带哈希的 ConfigMap 引用会触发滚动更新。旧 ConfigMap 确认无人引用后单独清理 |
| **停止/重启** | 停采集删 Alloy DaemonSet；停 Loki/Grafana 缩为 0。**不要**用 `kubectl delete -k` 整目录或删命名空间来"重启" |
| **镜像版本** | 固定为 Alloy v1.11.0 / Loki 3.6.0 / Grafana 12.2.0。部署前按企业镜像扫描策略审核并镜像到内网 |
| **迁移 S3** | 保留 filesystem 的旧 schema 时间段，为未来日期新增 S3 schema，并验证旧时间段仍可查询；不能只改存储类型后删旧 PV |

## 12. 多节点真实业务集群实测补充（17 节点 ARM64，2026-09）

§1–§11 来自**单节点**集群（`dmdb`，控制面与业务同机）。之后用同一套清单在一个**17 节点、有真实业务在跑**的集群（`datax`）上又做了一遍，本节记录新增结论。清单本身没有改动，改的只有节点标签与 `nodeSelector`。

### 12.1 集群形态与放置决策

| 项 | 实测值 |
| --- | --- |
| 节点 | 17 个，全部 arm64，k8s **v1.36.2** |
| **可调度节点** | **只有 4 个**：`datax-01` / `datax-02` / `datax-03` / `ecs-zdz-pro-0022` |
| 其余 13 个 | `Ready,SchedulingDisabled`（已 cordon，但**仍在跑业务 Pod**） |
| `ecs-zdz-pro-0022` | 带 `dedicated=gateway:NoSchedule` 污点 |
| 业务 | `deer-flow` 约 60–63 个 Pod（30–34 Running / 27 Completed / 2 Error，全程不变） |
| 存储节点 | `datax-03`，数据盘 `/dev/vdb1` **3.0T（可用 2.7T）** |
| 网络 | firewalld `inactive` + `disabled`，`FORWARD icmp-host-prohibited` = 0，coredns `1/1` |

最终放置：**Alloy 恰好 4 个 Pod**（4 个可调度节点各一个）；**Loki + Grafana 都在 `datax-03`**。

### 12.2 ⚠️ 用 tolerations 收窄 DaemonSet 是**无效**的，必须用 `nodeSelector`

**DaemonSet 控制器会无条件给 Pod 追加一批容忍**，其中包含 `node.kubernetes.io/unschedulable:NoSchedule`（还有 `not-ready` / `unreachable` / `disk-pressure` / `memory-pressure` / `pid-pressure`）。后果是：**已 cordon 的节点照样会起 Alloy Pod，无论你写什么 tolerations**；即使只保留自己的专用污点容忍，控制器也会把 `unschedulable` 加回去。

本次踩坑经过：Alloy 按全部节点部署（`desired=18`，cordon 不影响 DS 期望副本计算），为了让日志只来自 4 个节点先尝试删除自定义污点容忍——**无效**，rollout 一直停在：

```
4 of 18 updated pods available
```

正确做法是节点白名单（`nodeSelector`）：

```sh
for n in datax-01 datax-02 datax-03 ecs-zdz-pro-0022; do
  kubectl label node "$n" deerflow-log-agent=true --overwrite
done
kubectl get nodes -l deerflow-log-agent=true

kubectl apply -k .                                   # alloy.yaml 增加 spec.template.spec.nodeSelector
kubectl -n observability get ds alloy -o jsonpath='{.status.desiredNumberScheduled}{"\n"}'   # 18 -> 4
kubectl -n observability get pods -l app=alloy -o wide
```

> **判断是否生效看 `desiredNumberScheduled`，不要看 Pod 是否 Running。** cordon 节点上的旧 Pod 不会被驱逐也不会被 DS 缩容删除，会一直留在那里让 rollout 永远差几格，需要手工删：
>
> ```sh
> kubectl -n observability get pods -l app=alloy -o wide          # 找出不在白名单节点上的
> kubectl -n observability delete pod <那个 pod>
> ```

**先确认被排除的节点上没有需要采集的业务**。本次 13 个 cordon 节点上跑的是已完成/低优先业务，`deer-flow` 的 Pod 只在 4 个可调度节点上（分布 25/20/14/1），所以收窄不丢业务日志。

### 12.3 镜像要逐节点分发

`imagePullPolicy: IfNotPresent` 按**每个节点本地**的 containerd 缓存判断，**不跨节点共享**。单节点只导一次即可；多节点上每个 Alloy 目标节点都必须有镜像。

```sh
# 1) 在一个能出网的节点上按 §4.4 拉取并重打成 docker.io/grafana/*
sudo ctr -n k8s.io images list | grep -E 'docker.io/grafana/(alloy|loki|grafana)'

# 2) 导出（本次实测 tar 368,080,384 字节）
sudo ctr -n k8s.io images export /tmp/observability-arm64.tar \
  docker.io/grafana/alloy:v1.11.0 docker.io/grafana/loki:3.6.0 docker.io/grafana/grafana:12.2.0
sha256sum /tmp/observability-arm64.tar

# 3) 分发 + 导入 + 逐节点核对（应各为 3）
for h in datax-01 datax-02 datax-03 ecs-zdz-pro-0022; do
  scp /tmp/observability-arm64.tar "$h":/tmp/
  ssh "$h" 'sudo ctr -n k8s.io images import /tmp/observability-arm64.tar >/dev/null && \
            sudo ctr -n k8s.io images list -q | grep -c "^docker.io/grafana/"'
done
```

**datax 上出现过一个瞬时证书错误**，值得记录以免误判：

```
ctr: ... x509: certificate signed by unknown authority
```

证书签发者是陌生的中间 CA（不是企业 MITM CA `CN=cnooc`），**重试即成功**——判定为企业代理的降级/兜底证书，不是仓库不可信，也不是镜像源故障。对照：`registry.aliyuncs.com` 在该环境**始终**校验通过，`docker.m.daocloud.io` 偶发。遇到时**先重试一次**再下结论。

### 12.4 容量：先量日增量，再动手脚

```sh
# 5 分钟窗口量摄取速率（用 distributor 的累计字节）
A=$(curl -s --noproxy '*' http://127.0.0.1:3100/metrics | awk '/^loki_distributor_bytes_received_total/ {s+=$2} END{print s}')
sleep 300
B=$(curl -s --noproxy '*' http://127.0.0.1:3100/metrics | awk '/^loki_distributor_bytes_received_total/ {s+=$2} END{print s}')
awk -v a="$A" -v b="$B" 'BEGIN{printf "%.0f B/s  ≈ %.1f GB/day\n",(b-a)/300,(b-a)/300*86400/1024/1024/1024}'
```

本次实测：**243,802 B/s ≈ 21.1 GB/天** → 30 天约 **632 GB**，落在 2.7T 可用空间里余量充足，**`retention_period: 720h` 不需要调整**。

**但必须做归因，否则容量结论不可移交。** 用 `bytes_over_time` 按 `app` 分组：

```bash
curl -s --noproxy '*' -G http://127.0.0.1:3100/loki/api/v1/query \
  --data-urlencode 'query=sum by (app) (bytes_over_time({namespace="deer-flow"}[1h]))' | head -c 2000
```

本次结果：**`app="app"`（`weknora-app`）约 295.3 MiB，占总量 ~305 MiB 的 97%**；在存储侧也一致——单条 stream `chunks/fake/5f3cffd46f87bf10`（即 `weknora-app`）占 **119M / 134M ≈ 89%** 的 chunk 体积。

> **结论与建议：把 `weknora-app` 的日志量反馈给业务方。** 排除它之后，30 天需求从约 632 GB 降到约 **7.5 GB**，容量问题立刻从"要规划"变成"不用管"。这也是唯一能在不牺牲可观测性的前提下真正省下容量的动作——本文档记录的是事实与量级，是否降噪由业务方决定。

### 12.5 验收结果（多节点）

§9 的第 1–4 项全部通过，证据：

| # | 做法 | 结果 |
| --- | --- | --- |
| 1 | 造标记日志 Pod，查 `{namespace="deer-flow"} \|= "<marker>"` | 命中 **12** 条 |
| 2 | 删除该 Pod 后再查同一标记 | 仍命中 **18** 条（删 Pod 前新写入的也算） |
| 3 | `rollout restart daemonset/alloy` 后：新标记 + 老标记 | 新 **11** 条 + 老 **18** 条；Alloy 仍为 **4** 个 Pod |
| 4 | 删除 Loki Pod 后重建，查重启**前**写入的标记 | 仍命中（`tick=20…16`，`returned_lines=5`）；同时能查到重启后新产生的业务日志 |
| 5 | — | 见下 |

**第 5 项在 datax 上按"落盘 + 容量 + WAL 循环"收口**（未做 §9 原定义的"Loki 缩 0 补发"）：

| 判据 | 结果 |
| --- | --- |
| 落盘位置 | `/opt/deerflow-observability/loki` → `/dev/vdb1` 3.0T，**不是系统盘** |
| 容量 | 剩 2.7T vs 30 天约 632 GB → 通过 |
| WAL 循环 | **172M → 82M → 53M**，段 06/07/08/09 依次删除，`checkpoint` 005→008→009 推进 → 通过 |
| 持久性旁证 | Loki 目录 mtime（10:13）早于当前 Pod 启动时间（10:48），删 Pod 后数据在文件系统层保留 |

> §9 的"Loki 缩 0 期间日志补发"这一项在 `dmdb` 上已验证（30 条全部补发），**在 datax 上未做**，如需该项正式通过需另开维护窗口。

### 12.6 本次新增的四个操作坑

1. **`sudo` 不展开通配符。** `sudo du -sh /opt/.../loki/*` 里的 `*` 由**普通用户**的 shell 展开，而该目录属主 `10001`、权限 `drwxrws---`，展开失败后字面量被传给 `sudo`，报出误导性的 `No such file or directory`。用 `sudo sh -c '...'` 让 root 的 shell 展开。本次在同一件事上踩了两次（另一个是 `sudo ls /var/log/pods/observability_loki-*/loki/`）。

2. **残留 `kubectl port-forward` 会制造假阴性。** 旧进程继续占着本地端口并指向**已删除的旧 Pod**，新的 forward 启动后立即 `Exit 1`，而 `curl` 仍然连上那个死目标 —— 于是"验收第 4 项"被误判为 FAIL，实际数据完好。**每次 forward 前 `pkill -f 'port-forward'` 并确认 `ps -ef | grep '[p]ort-forward' | wc -l` 为 0。**

   反向的坑同样存在：**forward 已经死掉时，`curl -s` 会把"连接被拒"也一起静默**，stdout/stderr 双双为空，看起来就像"查询返回空结果"，会被误判成数据问题（本次就发生了，白查一轮）。**校验脚本里一律用 `curl -sS -w '\nHTTP=%{http_code} bytes=%{size_download}\n'`**，并在同一个命令块里先断言 forward 进程数为 1、再 `cat` 一次它的日志。

3. **Grafana Secret 的 key 是 `password`（不是 `admin-password`）。** 取错 key 时 `jsonpath` 返回空串，`base64 -d` 得到空密码，Grafana 回 `401 {"message":"Invalid username or password","messageId":"password-auth.failed"}` —— 这是取值错，不是密码错、不是 Grafana 故障。先 `echo "pw_len=${#GPW}"` 确认非空。

4. **Grafana 数据源的 uid 是 `deerflow-loki`，不是 `loki`。** 用 `/api/ds/query` 校验时 uid 写错会得到"数据源不存在"类错误；uid 以清单 `datasources.yaml` 为准。

5. **`kubectl logs` 看不到启动行 ≠ 日志被轮转掉了。** 本次一度据此推断"Loki 自身 stdout 被 kubelet 轮转掉"，实际量一下就知道是错的：`0.log` 共 **350 行 / 85 KB / 覆盖 17 分钟 ≈ 20 行/分钟**，单文件、无 `.log.1`，**远不及 10Mi 轮转阈值**，且第 1 行就是 `msg="Starting Loki" version="(version=3.6.0..."`。**先量文件大小与行数，再下结论。**

### 12.7 Grafana 数据源校验（不开 UI，纯 API）

§9 只要求"日志查得到"，但**"能查到"和"Grafana 里也能查到"是两件事**（后者多一条 `Grafana proxy → http://loki:3100` 的链路）。用 API 收掉：

```sh
pkill -f 'port-forward service/grafana'
kubectl -n observability port-forward service/grafana 3000:3000 >/tmp/gf-pf.log 2>&1 &
sleep 5
ps -ef | grep '[p]ort-forward' | wc -l    # 必须为 1；为 0 就说明 forward 没起来，后面必然全错
cat /tmp/gf-pf.log                        # 应只有 "Forwarding from 127.0.0.1:3000 -> 3000"

GPW=$(kubectl -n observability get secret grafana-admin -o jsonpath='{.data.password}' | base64 -d)
echo "pw_len=${#GPW}"                                    # 必须非空（本次 20）

echo "--- 1) Grafana 自身健康（无需认证）---"
curl -sS http://127.0.0.1:3000/api/health; echo

echo "--- 2) 数据源是否已自动配置 ---"
curl -sS -u "admin:$GPW" http://127.0.0.1:3000/api/datasources; echo

echo "--- 3) 经 Grafana 代理真的查一次 Loki（端到端）---"
NOW=$(date +%s%3N); FROM=$((NOW-900000))
cat > /tmp/dsq.json <<EOF
{"queries":[{"refId":"A","datasource":{"type":"loki","uid":"deerflow-loki"},"expr":"{namespace=\"deer-flow\"}","maxLines":3}],"from":"$FROM","to":"$NOW"}
EOF
cat /tmp/dsq.json; echo
curl -sS -w '\nHTTP=%{http_code} bytes=%{size_download}\n' \
  -u "admin:$GPW" -H 'Content-Type: application/json' \
  --data-binary @/tmp/dsq.json http://127.0.0.1:3000/api/ds/query | head -c 1500
```

> **用 heredoc 写请求体，不要手写转义。** 这个 body 里有嵌套的 `\\"`，手工拼引号极易写坏；`cat > file <<EOF` 再 `--data-binary @file` 最稳。
> **`curl` 一律用 `-sS -w 'HTTP=%{http_code}'`，不要只用 `-s`。** `-s` 会**连错误信息一起静默**：`port-forward` 已经死掉时连接被拒，stdout 与 stderr 都是空的，看起来就像"查询返回空结果"，会被误判成数据问题。

**本次实测结果**：

| 步骤 | 结果 |
| --- | --- |
| `/api/health` | `{"database":"ok","version":"12.2.0"}` |
| `/api/datasources` | `[{"uid":"deerflow-loki","name":"DeerFlow Logs","type":"loki","url":"http://loki:3100","isDefault":true,"readOnly":true}]` |
| 经 Grafana 代理查询 Loki | `status: 200`，返回 frames；`Ingester: total lines sent = 3`、`total chunks matched = 51`、`exec time = 0.076s`，`executedQueryString` = `Expr: {namespace="deer-flow"}` |
| 管理员账号 | `GF_SECURITY_ADMIN_USER` = 字面量 `admin`；`GF_SECURITY_ADMIN_PASSWORD` 来自 `grafana-admin/password` |

> 这**不等于**已经给用户开通了 UI 访问：浏览器访问方式仍见 §10（本次因 SSH `PermitOpen` 白名单限制，UI 访问按用户要求暂缓）。

## 附录 A：本次实测环境与关键数字

| 项 | 值 |
| --- | --- |
| 节点 | 麒麟 Linux 高级服务器 V10 SP1 (Tercel) / aarch64 / 128 核 / 509G 内存 |
| 集群 | 单节点 kubeadm，k8s **v1.36.2**，containerd **1.7.27**，Flannel，控制面与业务同节点 |
| 地址 | 业务网卡 `bond0` = `192.168.110.14`；`/` 444G（可用 215G），**`/opt` 30T（可用 28T）** |
| 业务 | `deer-flow` 命名空间 26 个 Pod（17 Running / 7 Completed / 1 ErrImageNeverPull / 1 Pending） |
| 日志量 | `/var/log/pods` 共 473M（75 天） |
| 镜像源 | `docker.m.daocloud.io` 可达；`registry-1.docker.io` **不可达**（`Proxy CONNECT aborted`） |
| 镜像 | alloy 134.9 MiB / loki 36.2 MiB / grafana 179.9 MiB |
| 存储 | SC `deerflow-logs-local`（no-provisioner）；PV `deerflow-logs-loki` 100Gi、`deerflow-logs-grafana` 5Gi，均 `Retain` |
| 稳态体积 | Loki 约 9M、Grafana 约 1.2M、Alloy WAL 约 1.3M |
| 验收结果 | 五项全过；`smoke-004` 在 Loki 离线期间的 30 条日志恢复后全部补发 |

## 附录 B：故障速查表

| 现象 | 根因 | 解法 |
| --- | --- | --- |
| `ctr pull` 报 `read tcp <本机>-><公网IP>:443: connection reset by peer` | `sudo` 清掉了代理，`ctr` 直连（下载在客户端进程内） | 显式 source 代理文件，见 §4.4 |
| Pod `ImagePullBackOff`，Events 指向 Docker Hub | 只拉了镜像源名字，没重打成 `docker.io/grafana/...` | `ctr images tag`，见 §4.4 |
| CVS/PV 一直 `Pending` | 节点未打 `deerflow-log-storage=true` 标签；或目录不存在 | 打标签 + 预建目录（属主 10001 / 472） |
| Loki/Grafana `CrashLoopBackOff` + `permission denied` | 数据目录属主不对 | 按 §4.2 修正 |
| `dry-run` 报 `namespaces "observability" not found` | dry-run 在命名空间创建前执行 | 先 `apply -f namespace.yaml`，见 §5.2 |
| Alloy `1/1 Running` 但 Loki 无数据 | Pod Ready ≠ 已送达；Loki 未就绪或地址不通 | 查 Loki API + Alloy 指标，见 §7 |
| Alloy 大量 `error sending batch` + WAL 持续增长 | 首装历史回灌，历史条目被 Loki 拒绝 | 加 `tail_from_end = true` 并清位置记录，见 §8 |
| 日志里 `entry too far behind` / `has timestamp too old` | ingester 发送窗口 / `reject_old_samples_max_age` 拒绝历史条目 | 同上；这类条目本来就不该入库 |
| Alloy 重试期间出现 `connection refused` | Loki 尚在启动或已缩容 | **正确的重试行为**，非故障；恢复后应自动补发 |
| `ssh -L` 报 `administratively prohibited` | sshd 的 `PermitOpen` 白名单不含该目标 | 改用白名单内目标，见 §10.2 |
| CoreDNS 永远 `0/1`、集群 DNS 全废 | firewalld 的兜底 REJECT 排在 CNI 规则之前 | 关闭 firewalld，或把 `cni0`/`flannel.1` 加入 trusted 域 |
| Alloy rollout 卡在 `N of 18 updated pods available`，cordon 节点上仍有 Alloy Pod | DS 控制器**无条件**追加 `unschedulable:NoSchedule`，tolerations 收窄无效 | 改用 `nodeSelector` 白名单 + 手工删旧 Pod，见 §12.2 |
| 查询"查不到"，但 Loki 里其实有数据 | 残留 `kubectl port-forward` 占着本地端口并指向已删除的旧 Pod | `pkill -f 'port-forward'`，确认计数为 0 后再 forward，见 §12.6 |
| `sudo du -sh <目录>/*` 报 `No such file or directory`，但目录确实存在 | `*` 由普通用户 shell 展开，目录无列权限 → 字面量传给 `sudo` | `sudo sh -c 'du -sh <目录>/*'`，见 §12.6 |
| Grafana API 返回 `401 Invalid username or password`，但密码是对的 | Secret key 取错（是 `password`，不是 `admin-password`），`jsonpath` 返回空串 | 先 `echo "pw_len=${#GPW}"` 验非空，见 §12.6 |
| `kubectl logs` 里看不到 Loki 的启动行，疑似日志被轮转 | 先看错了地方：文件其实完整（350 行/17 分钟，远不及 10Mi 轮转阈值） | 直接读 `/var/log/pods/observability_loki-*/loki/0.log` 并量行数/体积，见 §12.6 |
| `ctr pull` 报 `x509: certificate signed by unknown authority`，签发者不是企业 CA | 企业代理返回降级/兜底证书（瞬时） | **先重试一次**；`registry.aliyuncs.com` 在该环境始终可验，见 §12.3 |

## 附录 C：多节点集群（datax）实测数字

| 项 | 值 |
| --- | --- |
| 节点 | 17 个 arm64 / k8s v1.36.2；可调度 4 个，13 个 `Ready,SchedulingDisabled` |
| 业务 | `deer-flow` 约 60–63 Pod（30–34 Running / 27 Completed / 2 Error，全程未变） |
| 放置 | Alloy 4 个 Pod；Loki + Grafana 在 `datax-03` |
| 存储 | `/dev/vdb1` 3.0T / 可用 2.7T；目录 `/opt/deerflow-observability/{loki,grafana}`（属主 10001 / 472） |
| 摄取速率 | **243,802 B/s ≈ 21.1 GB/天**（30 天约 632 GB） |
| 日志量归因 | `app="app"`（weknora-app）占 `bytes_over_time` 的 **97%**；存储侧该 stream 占 chunk 的 **89%**（119M/134M） |
| WAL 循环 | 153M → 172M → **82M** → **53M**；`checkpoint` 005→008→009 |
| 镜像分发 | `observability-arm64.tar` 368,080,384 字节；4 个节点各导入成功（三个镜像名） |
| 验收 | 第 1–4 项通过（12 / 18 / 新 11+老 18 / 重启前后均命中）；第 5 项按存储与 WAL 收口 |
| Grafana | `/api/health` OK（12.2.0）；数据源自动配置为 `uid=deerflow-loki` / `DeerFlow Logs` / `http://loki:3100` / 默认；经 Grafana 代理 `/api/ds/query` 端到端查询 `status: 200`（lines sent=3 / chunks matched=51 / 76ms）；UI 访问按用户要求暂缓 |
| Loki 自身日志 | `/var/log/pods/observability_loki-*/loki/0.log`：350 行 / 85 KB / 17 分钟（≈20 行/分钟），无轮转 |

## 相关文档

- **把日志接到集群外的 Grafana**（`kubectl port-forward` + 最小权限 RBAC + systemd 常驻）：[observability-external-grafana-loki-access.md](./observability-external-grafana-loki-access.md)
  —— 含"为什么不能直连 ClusterIP"和"为什么 SSH 隧道也不可用"的完整排查结论。
- 组件清单与边界：[`k8s/local-pvc/observability/README.md`](../k8s/local-pvc/observability/README.md)
- 麒麟 V10 单节点集群搭建（本栈的运行底座）：`docs/kingbase-arm64-single-node-cluster-setup.md`
  —— 该文档在 `codex/feature-kingbase-upstream-integration` 分支上，本分支不包含，故不作相对链接；其中 §7 的 firewalld 前置条件与本栈同样相关。
- 官方参考：
  - https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/
  - https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/
  - https://grafana.com/docs/loki/latest/operations/storage/retention/
  - https://grafana.com/docs/loki/latest/operations/storage/filesystem/
