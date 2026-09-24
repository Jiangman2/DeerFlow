# 让**集群外的 Grafana** 读取 datax 集群的 Loki 日志

> 场景：datax 集群（17 节点 ARM64，k8s v1.36.2）里已经有跑通的 Alloy + Loki 日志栈，
> 现在要把日志接到**另一台机器上已有的 Grafana**（`Prometheus-01`，`http://10.77.76.207:3000`），
> 而不是只用集群内那个自带的 Grafana。
>
> 本文所有命令与数字均为实机验证结果。

## 1. 结论先行

采用的方案：**在外部 Grafana 所在主机上跑 `kubectl port-forward`，把每个集群的 Loki 各引到一个本机回环端口；Grafana 里每个集群一个数据源。**

**本次交付：两个集群（datax 与 dmdb）都已打通，跑在 systemd 常驻服务 + 探活看门狗上（见第 8 节）。**

| 集群 | kube-apiserver 地址 | 外部主机回环端口 | Grafana 数据源 URL |
| --- | --- | --- | --- |
| **datax**（17 节点 ARM64） | `https://192.168.111.2:6443`（节点直连） | `127.0.0.1:3100` | `http://127.0.0.1:3100` |
| **dmdb**（单节点 ARM64） | `https://10.77.78.224:6443` **+ `tls-server-name: 192.168.110.14`**（办公网映射） | `127.0.0.1:3101` | `http://127.0.0.1:3101` |

| 维度 | 情况 |
| --- | --- |
| 监听端口 | **每个集群只有本机回环一个端口**（`kubectl` 进程持有），同网段任何其它机器都连不上 |
| 集群改动 | 每个集群的 `observability` 命名空间各新增 4 个小对象（SA / Role / RoleBinding / token Secret） |
| 日志栈本体 | **零改动**（不改清单、不改 Loki/Grafana/Alloy、不加 Service 类型） |
| 传输 | `kubectl` → kube-apiserver（TLS + Bearer token），不由明文 HTTP 承载 |
| 凭证 | 命名空间级最小权限：只能 `get/list services,pods,endpoints,endpointslices` + `create pods/portforward` |
| 区分两个集群 | 两边命名空间与应用名**完全一样**，只能靠日志里的 `node` 标签：`node="dmdb"` vs `node=~"datax-.*"` |

```
浏览器 ──> Prometheus-01:3000 (Grafana, 原生 systemd 服务)
                │
                ├─ 数据源 "DeerFlow Logs (datax)" ──> http://127.0.0.1:3100 ─┐
                └─ 数据源 "DeerFlow Logs (dmdb)"  ──> http://127.0.0.1:3101 ─┤
                                                                          │  kubectl port-forward
                        ┌─────────────────────────────────────────────────┘  （Kubernetes API over TLS，不是 SSH、不是 ClusterIP）
                        ▼                                        ▼
        https://192.168.111.2:6443                     https://10.77.78.224:6443
        datax kube-apiserver                            dmdb kube-apiserver
        （节点直连，证书 SAN 含该 IP）                    （办公网映射，tls-server-name=192.168.110.14）
                        ▼                                        ▼
        kubelet@datax-03:10250 ──> Loki pod             kubelet@dmdb:10250 ──> Loki pod
```

## 2. 为什么不用"直连 ClusterIP"（含排查结论）

直觉上最省事的做法是：在 `Prometheus-01` 上加一条路由把 service CIDR 引到某台节点，数据源直接填 `http://10.101.111.97:3100`。**本环境实测走不通**，过程与证据如下，供后来者判断是否可以复用别的手段。

### 2.1 事实基线

| 项 | 值 |
| --- | --- |
| 外部 Grafana 主机 | `Prometheus-01`，办公网 `10.77.76.207`，**同时就在集群网段** `192.168.111.40/24` |
| Loki | ClusterIP `10.101.111.97:3100`；pod `10.244.2.90` 在 `datax-03`（`192.168.111.18`） |
| 节点上访问 ClusterIP | 正常（`/ready`、`/labels` 均 200） |
| `Prometheus-01` → `192.168.111.2` / `.18` | ping 0.3ms、`ssh 22` 三次握手成功 |
| `Prometheus-01` → `10.101.111.97` | `ping` 100% 丢包、`curl` **`Connection timed out`** |
| kube-proxy | **iptables 模式**（`mode: ""`），`masqueradeAll: false`，`clusterCIDR: 10.244.0.0/16` |

### 2.2 排查过程中被推翻的三个假设（记录下来免得重复走）

1. ~~`KUBE-PROXY-FIREWALL` 拦了外部访问~~ —— 该链**是空的**（只有 `-N`），无规则。
2. ~~外部源到 ClusterIP 不做 SNAT，导致回包非对称~~ —— nat 表里明确有
   `KUBE-SVC-xxx ! -s 10.244.0.0/16 -d 10.101.111.97/32 ... -j KUBE-MARK-MASQ`，
   **外部源会被 masquerade**，回包路径不是问题；因此"必须把路由指到承载 pod 的那台节点"这个约束**不成立**。
3. ~~集群有意禁止集群外访问 ClusterIP~~ —— filter 表 `KUBE-SERVICES` 里只有两条与本服务无关的
   `REJECT`（某个没有 endpoint 的 sandbox Service），没有针对 ClusterIP 的 DROP。

内核侧也没有丢包痕迹：`dmesg` 无 martian，`IpInAddrErrors=0`、`IpInDiscards=0`、`IpInHdrErrors=1`。

### 2.3 实测结果

在 `Prometheus-01` 上临时加 `/32` 路由并请求：

```sh
sudo ip route add 10.101.111.97/32 via 192.168.111.18 dev enp3s0   # 指向承载 pod 的 datax-03
curl -sS -m 6 http://10.101.111.97:3100/ready
# curl: (28) Connection timed out after 6001 milliseconds

sudo ip route replace 10.101.111.97/32 via 192.168.111.2 dev enp3s0  # 改为 datax-01
curl -sS -m 6 http://10.101.111.97:3100/ready
# curl: (28) Connection timed out after 6001 milliseconds
```

**两条路由都超时**。`datax-01` 上的 tcpdump 因抓包窗口与操作时序错开而**未捕获到包**，所以"包是否到达节点"**没有定论**（这一点必须如实记录，不要当成已查明）。

### 2.4 SSH 隧道同样不可用

本环境**所有节点与 `Prometheus-01` 都禁用了 SSH 转发**，且是同一份基线的同一行：

```
$ sudo sshd -T | grep -i allowtcpforwarding
allowtcpforwarding no
$ sudo grep -n AllowTcpForwarding /etc/ssh/sshd_config
155:AllowTcpForwarding no          # datax-01 / datax-02 / datax-03 / ecs-zdz-pro-0022 / Prometheus-01 完全一致
```

`AllowTcpForwarding` 约束的是**服务端**接受 `-L`/`-R` 请求，因此 `Prometheus-01 → 节点` 与 `节点 → Prometheus-01` 两个方向的隧道**都不可用**。

> 理论上可以用 `nc`/`python` 走 SSH 的 **exec 通道**绕过该限制（exec 通道不受 `AllowTcpForwarding` 约束），但那是刻意绕开一条覆盖 18 台机器的安全基线，**本方案不采用**。

### 2.5 为什么不用 NodePort / hostPort

Loki 是 `auth_enabled: false`（**完全无认证**）。`Prometheus-01` 本身就位于集群网段 `192.168.111.0/24`，一旦用 NodePort 暴露，**整个 VLAN 上任何主机都能不经 Grafana 登录直接读取全部业务日志**，还能向 `/loki/api/v1/push` 写脏数据。所以「开端口」这条路在本环境被明确否决。

**最终结论：拦截点大概率在集群之外的网络/安全设备上，不是集群配置能解决的。** `kubectl port-forward` 绕开了整条 ClusterIP 路径（它走 apiserver → kubelet → **pod IP**），因此不受影响。

## 3. 前置条件（外部主机侧）

第 5 节的操作需要外部主机具备：

| 条件 | 本次实测值 |
| --- | --- |
| 能访问 kube-apiserver | `192.168.111.2:6443` TCP 可达，`/version` 返回 200 |
| 有 `kubectl` | **原本没有**，从节点拷贝二进制过来即可（`/usr/local/bin/kubectl`，v1.36.2） |
| 有 `sudo` | 免密 |
| Grafana 形态 | **原生 systemd 服务**（`grafana.service`，监听 `*:3000`）—— 这决定了数据源可以填 `127.0.0.1` |

> **若 Grafana 跑在容器里且不是 `--network host`**，宿主机回环对它不可见：此时要么把 `port-forward` 绑到 docker 网桥地址（`--address 172.17.0.1`）、数据源填 `http://172.17.0.1:3100`；要么给容器加 `--network host`。**部署前必须确认这一条**，否则数据源永远连不上。

**apiserver 证书 SAN 必须包含你准备使用的地址**（否则 TLS 校验失败）：

```sh
sudo openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -text | grep -A3 'Subject Alternative Name'
# 本次：DNS:datax-01, datax-02, datax-03, kubernetes, ... IP Address:10.96.0.1,
#       IP Address:192.168.111.2, IP Address:127.0.0.1, IP Address:192.168.111.37, IP Address:192.168.111.18
```

确认包含 `192.168.111.2` → 可以从外部直连，**不需要** `insecure-skip-tls-verify`。

## 4. 集群侧：最小权限 RBAC + 长期 token

清单见 [`k8s/local-pvc/observability/external-access/`](../k8s/local-pvc/observability/external-access/)：

```sh
kubectl apply -f k8s/local-pvc/observability/external-access/rbac.yaml
kubectl apply -f k8s/local-pvc/observability/external-access/token-secret.yaml
sleep 6
kubectl -n observability get secret loki-portforward-token -o jsonpath='{.data.token}' | wc -c
# 1260（base64）→ 解码后 944 字符
```

**token 是长期有效的**（依赖集群仍支持 legacy `kubernetes.io/service-account-token` Secret 被 token controller 填充；k8s v1.36.2 实测可用）。若某天该方式失效（`.data.token` 为空），改用有期限的 token：

```sh
kubectl -n observability create token loki-portforward --duration=8760h
```

权限自查（**注意下面这个坑**）：

```sh
kubectl --kubeconfig <K> auth can-i create pods/portforward -n observability   # 返回 no
kubectl --kubeconfig <K> auth can-i list pods -n observability                # 返回 yes
kubectl --kubeconfig <K> auth can-i create pods/portforward -n deer-flow      # 返回 no（正确）
kubectl --kubeconfig <K> auth can-i get secrets -n observability              # 返回 no（正确）
```

> **`can-i` 对子资源 `pods/portforward` 会误报 `no`，而实际转发是通的。** 判断权限是否够用，**以真实操作为准**（第 6 节的手工验证），或用 `kubectl auth can-i --list -n observability` 看生效规则。

## 5. 生成受限 kubeconfig 并放到外部主机

### 5.1 ⚠️ 两个必须避开的坑

1. **`server` 必须是节点真实 IP，不能是 `127.0.0.1:7443`。**
   本环境 `cnooc` 的 kubeconfig 指向 `https://127.0.0.1:7443` —— 那是 **datax-01 上一台 nginx 反代**（`ss -tlnp` 可见 32 个 nginx worker）。回环地址在别的机器上指的是它自己，搬过去必然不可用。

2. **CA 直接用集群权威文件，不要用被反代改写过的那份。**
   该 kubeconfig 内嵌的 CA 解出来**不是合法 x509**（`openssl x509` 解析失败），因为它对应的是 nginx 那套证书。改用：

```sh
SERVER=https://192.168.111.2:6443
CA=$(sudo base64 -w0 /etc/kubernetes/pki/ca.crt)     # subject=CN=kubernetes
TOKEN=$(kubectl -n observability get secret loki-portforward-token -o jsonpath='{.data.token}' | base64 -d)
```

### 5.2 生成

```sh
cat > /tmp/loki-pf.kubeconfig <<EOF
apiVersion: v1
kind: Config
clusters:
- name: datax
  cluster:
    server: $SERVER
    certificate-authority-data: $CA
users:
- name: loki-portforward
  user:
    token: $TOKEN
contexts:
- name: loki-portforward@datax
  context:
    cluster: datax
    user: loki-portforward
    namespace: observability
current-context: loki-portforward@datax
EOF
chmod 600 /tmp/loki-pf.kubeconfig
```

### 5.3 交换文件并验证

```sh
# 生成端：备好要传的东西
cp "$(command -v kubectl)" /tmp/kubectl-bin && chmod 755 /tmp/kubectl-bin

# 外部主机：拉取（scp 会要密码，**这一步单独执行**，别和其它命令一起粘）
scp cnooc@192.168.111.2:/tmp/kubectl-bin /tmp/kubectl-bin
scp cnooc@192.168.111.2:/tmp/loki-pf.kubeconfig /tmp/loki-pf.kubeconfig

sudo install -m 0755 /tmp/kubectl-bin /usr/local/bin/kubectl
mkdir -p ~/.kube
install -m 0600 /tmp/loki-pf.kubeconfig ~/.kube/loki-pf.kubeconfig
rm -f /tmp/kubectl-bin /tmp/loki-pf.kubeconfig

kubectl version --client                       # v1.36.2
kubectl --kubeconfig ~/.kube/loki-pf.kubeconfig -n observability get svc loki
kubectl --kubeconfig ~/.kube/loki-pf.kubeconfig -n observability get pods -l app=loki -o wide
```

**这一步的意义**：它与最终方案走**完全相同**的地址与凭证，唯一变量是"从哪台机器发起"。

### 5.4 第二个集群（dmdb）：`server` 用映射地址 + `tls-server-name`

dmdb 是**另一个集群**（单节点，业务网 `192.168.110.14`），从外部 Grafana 那台机器看过去的情况与 datax 完全不同：

| 探测（从 `Prometheus-01`） | 结果 |
| --- | --- |
| `192.168.110.14`（dmdb 业务网 IP）ping / `:22` / `:6443` | **全部 FAIL** —— 两个网段不互通 |
| `10.77.78.224`（dmdb 的办公网映射） | ping OK（1ms）、**`:22` OK、`:6443` OK** |

所以 dmdb 必须走**映射地址**。但它的 apiserver 证书只签了业务网 IP：

```
X509v3 Subject Alternative Name:
    DNS:dmdb, DNS:kubernetes, DNS:kubernetes.default,
    DNS:kubernetes.default.svc, DNS:kubernetes.default.svc.cluster.local,
    IP Address:10.96.0.1, IP Address:192.168.110.14
```

直接连 `10.77.78.224` 会得到 `x509: certificate is valid for ..., not 10.77.78.224`。

**解法是 `tls-server-name`** —— 告诉客户端"我连的是映射地址，但请按真实 IP 校验证书"。**CA 校验与主机名校验都完整保留**，不是跳过校验：

```yaml
clusters:
- name: dmdb
  cluster:
    server: https://10.77.78.224:6443
    certificate-authority-data: <dmdb 的 /etc/kubernetes/pki/ca.crt，base64>
    tls-server-name: 192.168.110.14        # ← 关键
```

其余步骤（RBAC、token、scp、systemd、Grafana 数据源）与 datax **完全同构**，只是：

| 项 | datax | dmdb |
| --- | --- | --- |
| 服务名 | `loki-portforward` | `loki-portforward-dmdb` |
| kubeconfig | `~/.kube/loki-pf.kubeconfig` | `~/.kube/loki-pf-dmdb.kubeconfig` |
| 本机端口 | `3100` | `3101` |
| ClusterIP（核对用） | `10.101.111.97` | `10.103.180.172` |
| 日志 `node` 标签 | `datax-01`/`02`/`03`/… | `dmdb` |

> **两个映射地址的行为不一样，不要假设。** datax 的 `10.77.79.131` **只真转发了 22**（`80/2379/6443/10250` 都能完成 TCP 握手但没有任何响应）；dmdb 的 `10.77.78.224` **把 6443 也转了**。部署新集群前必须先 `nc -z <映射地址> 6443` 实测，并同时测业务网 IP —— 谁通用谁。

## 6. systemd 常驻 + 看门狗

`kubectl port-forward` 有两个必须处理的问题：

- 它**不是**长期隧道：网络抖动、apiserver 升级、pod 重建都可能让它退出；
- 更麻烦的是**"假活"**：进程还在、转发已断。只靠 `Restart=always` 救不了这种情况，所以再加一个探活看门狗。

四个 unit 见 [`external-access/`](../k8s/local-pvc/observability/external-access/)（**每个集群一个主服务**，看门狗共用一个）：

```sh
sudo cp k8s/local-pvc/observability/external-access/loki-portforward.service \
        k8s/local-pvc/observability/external-access/loki-portforward-dmdb.service \
        k8s/local-pvc/observability/external-access/loki-portforward-watchdog.service \
        k8s/local-pvc/observability/external-access/loki-portforward-watchdog.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now loki-portforward.service
sudo systemctl enable --now loki-portforward-dmdb.service
sudo systemctl enable --now loki-portforward-watchdog.timer
```

要点：

| 项 | 说明 |
| --- | --- |
| `User=cnooc` | 不用 root 跑 kubectl；`3100`/`3101` 都是高端口，无需特权 |
| `Restart=always` + `RestartSec=5` | 进程退出后 5 秒内自动拉起 |
| **看门狗一个 timer 探所有端口** | 形如 `for p in 3100:loki-portforward 3101:loki-portforward-dmdb`，`curl /ready` 失败就 `systemctl restart` 对应服务。**再接入第三个集群时只需在这个列表里加一项。** |
| `--address 127.0.0.1` | **必须显式**，确保只绑回环 |

**自愈验证（每个集群都要做一遍）**：

```sh
for u in loki-portforward loki-portforward-dmdb; do
  echo "--- $u ---"
  PID=$(systemctl show -p MainPID --value $u)
  sudo kill -9 "$PID"
  sleep 15
  systemctl is-active $u                       # active
  systemctl show -p MainPID --value $u         # 新 PID，与原来不同
done
curl -sS -m 5 -o /dev/null -w '3100 %{http_code}\n' http://127.0.0.1:3100/ready   # 200
curl -sS -m 5 -o /dev/null -w '3101 %{http_code}\n' http://127.0.0.1:3101/ready   # 200
journalctl -u loki-portforward-dmdb.service -n 12 --no-pager  # 可见 "Scheduled restart job, restart counter is at 1"
```

## 7. Grafana 数据源

在外部 Grafana（`http://10.77.76.207:3000`，12.0.1）里 **Configuration → Data sources → Add data source → Loki**，**每个集群加一个**：

**数据源 1（datax）**

| 字段 | 值 |
| --- | --- |
| Name | `DeerFlow Logs (datax)` |
| Type | **Loki** |
| URL | **`http://127.0.0.1:3100`** |
| Access | **Server (default)** —— 不要选 Browser |
| Auth / TLS | 全部留空（Loki 无认证，且只走本机回环） |

**数据源 2（dmdb）**

| 字段 | 值 |
| --- | --- |
| Name | `DeerFlow Logs (dmdb)` |
| Type | **Loki** |
| URL | **`http://127.0.0.1:3101`** |
| Access | **Server (default)** |
| Auth / TLS | 全部留空 |

两个都应显示 ***Data source connected and labels found***（本次已实测通过）。然后在 Explore 里查：

> **两个集群的命名空间、应用名、标签完全一样**，只能靠 `node` 区分：
>
> ```logql
> {namespace="deer-flow", node="dmdb"}          # 只看 dmdb
> {namespace="deer-flow", node=~"datax-.*"}     # 只看 datax
> ```

```logql
{namespace="deer-flow"}
{namespace="deer-flow"} |~ "ERROR|Traceback|Timeout"
{namespace="deer-flow", app="nginx"} |~ "upstream|connect\\(\\) failed"
```

> `127.0.0.1` 是**从 Grafana 服务端进程**解析的。本次外部 Grafana 是原生 systemd 服务，所以宿主回环就是它自己；容器化部署请看第 3 节的注意事项。

## 8. 验收清单（本次实测）

### datax

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 集群侧 RBAC 创建 | SA / Role / RoleBinding `created` |
| 2 | token Secret 被填充 | base64 1260 → 解码 944 字符（**长期有效**） |
| 3 | 权限边界 | `pods/portforward@observability` 实际可用；`@deer-flow` = `no`；`get secrets` = `no` |
| 4 | 外部主机访问 apiserver | `get svc loki` / `get pods` 正常，**无 x509 报错** |
| 5 | 监听范围 | `ss` 显示 **`127.0.0.1:3100`**（不是 `*:3100`） |
| 6 | Loki API | `/ready` = 200、`/labels` = 200 |
| 7 | 真实业务日志 | ✅ `{"app":"adapter", ..., "pod":"deer-flow-mineru-adapter-7f59bc8879-7l8jb","node":"datax-02"}` |
| 8 | 开机自启 | `loki-portforward.service` `enabled` |
| 9 | 进程被杀自愈 | `kill -9` 后 5 秒重启（PID 变化），`/ready` 恢复 200 |
| 10 | 看门狗 | timer 已启用，`NEXT` 有值 |
| 11 | **Grafana 数据源端到端** | ✅ `Save & test` 通过（*Data source connected and labels found*），Explore 查 `{namespace="deer-flow"}` 能出日志 |

### dmdb

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 到 dmdb 的可达性 | 业务网 `192.168.110.14` **全 FAIL**；办公网映射 `10.77.78.224` 的 `:22`/`:6443` **OK** |
| 2 | 集群侧 RBAC + token | 创建成功，token base64 1260 → 解码 944；ClusterIP `10.103.180.172` |
| 3 | **`tls-server-name` 生效** | `server=https://10.77.78.224:6443` + `tls-server-name=192.168.110.14` → `get svc loki` 正常，**无 x509 报错** |
| 4 | 监听范围 | `127.0.0.1:3101`（与 datax 的 `3100` 并存，互不干扰） |
| 5 | Loki API | `/ready` = 200、`/labels` = 200 |
| 6 | **数据归属鉴别** | ✅ `{"app":"web", ..., "pod":"skillhub-web-69c48f845d-6pvl5","node":"dmdb"}` —— 确认是 dmdb 而非 datax |
| 7 | 开机自启 | `loki-portforward-dmdb.service` `enabled` |
| 8 | 进程被杀自愈 | `kill -9` 后 `RestartSec=5s expired` → 新 PID → `/ready` 恢复 200 |
| 9 | 看门狗 | 双端点版本手工触发一轮 `Succeeded`，**无误重启**；timer `NEXT` 有值 |

## 9. 运维与排障

```sh
systemctl status loki-portforward.service
journalctl -u loki-portforward.service -n 50 --no-pager
systemctl list-timers loki-portforward-watchdog.timer --no-pager
sudo ss -tlnp | grep ':3100'
curl -sS -m 5 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3100/ready
```

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| Grafana 面板突然无数据，但 `systemctl is-active` 仍是 `active` | port-forward **"假活"** | 看门狗应在 60s 内重启；可手工 `systemctl restart loki-portforward` |
| `x509: certificate signed by unknown authority` | kubeconfig 里用了被 nginx 改写过的 CA（datax 的 `127.0.0.1:7443` 那套） | 改用 `/etc/kubernetes/pki/ca.crt`（第 5.1 节） |
| `x509: certificate is valid for ..., not <映射地址>` | 只能经映射地址访问，但 apiserver 证书只签了业务网 IP | 加 `tls-server-name: <证书里的真实 IP>`（第 5.4 节），**不要**用 `insecure-skip-tls-verify` |
| 服务 `status=203/EXEC` 且无限重启 | `ExecStart` 里的 `kubectl` 路径或 kubeconfig 在本机不存在 —— 通常是**命令块跑到了错误的机器上** | 先 `hostname` 确认；清理误装单元见下 |
| `connect: connection refused` / 超时到 `127.0.0.1:7443` | kubeconfig 的 `server` 还是回环地址 | 改成 `https://<节点IP>:6443` |
| 在 Grafana 里查到"错误的集群" | 两个集群的 `namespace`/`app` 标签完全一样 | 用 `node` 标签区分（第 7 节） |
| apiserver 地址变了（节点换 IP / 重建集群） | `server` 是写死的节点 IP | 更新 kubeconfig 后 `systemctl restart <对应服务>` |

**跨主机操作时，给每个命令块加主机守卫。** 本次就因为一段本该在外部主机执行、却粘到了被采集集群的机器上，导致那里装了一个 `203/EXEC` 无限重启的单元（未影响业务，但纯属多余）：

```sh
if [ "$(hostname)" = "Prometheus-01" ]; then
  ... 该主机上才该执行的命令 ...
else
  echo "!!! 当前主机是 $(hostname)，不是 Prometheus-01 —— 本段已跳过"
fi
```

误装后的清理（**在被误装的机器上**执行，注意只删单元、不动任何集群对象）：

```sh
sudo systemctl disable --now loki-portforward-dmdb.service
sudo systemctl reset-failed loki-portforward-dmdb.service
sudo rm -f /etc/systemd/system/loki-portforward-dmdb.service /etc/systemd/system/loki-portforward-watchdog.service
sudo systemctl daemon-reload
ls -l /etc/systemd/system/loki-portforward*    # 应无输出
```

## 10. 安全与撤销

- **凭证范围**：只能在本集群 `observability` 命名空间 `get/list services,pods,endpoints,endpointslices` 与 `create pods/portforward`；读不到 Secret，跨命名空间被拒（已实测）。
- **撤销**：`kubectl -n observability delete secret loki-portforward-token`（旧 token 立即失效），必要时同时删除 SA/Role/RoleBinding。
- **token 不过期**：方便，但泄露后长期有效。请把它当作"集群内一个只读日志通道的凭证"来管理（文件权限 `0600`、只放在指定主机）。
- **不要**为了让别的机器接入而把 Loki 改成 NodePort/LoadBalancer —— Loki 无认证，那等于把全部业务日志摊在网段上。要么每台机器各自建一条 port-forward，要么在前面加认证反代（本环境未实施）。
- **数据面**：`port-forward` 的数据经 apiserver 转发，TLS 保护；不经过明文 HTTP 的跨网段路径。

## 11. 遗留事项

1. **直连 ClusterIP 失败的真实拦截点未查明**：已排除 kube-proxy 侧（防火墙链为空、无 DROP、内核无丢包计数、外部源会被 masquerade）；`datax-01` 的 tcpdump 因时序问题未捕获到包。若后续要把它查清，建议在**网络设备的会话日志**上按 `192.168.111.40 → 10.101.111.97:3100` 查，而不是继续在节点上翻 iptables。
2. **集群内 Grafana 的 UI（datax 与 dmdb）暂不开放** —— 决定日期 2026-09。两个集群的日志都通过**集群外这台统一 Grafana** 消费，集群内那套 Grafana 只作为回退手段保留（ClusterIP、无外部入口、无风险）。因此：
   - **dmdb 的回环 80 保持空闲**，`PermitOpen 127.0.0.1:80` 那条路（见 [observability-logging-stack-deployment.md §10.2](./observability-logging-stack-deployment.md)）**未实施**，以后需要时再按该节操作即可；
   - **datax 集群内的 Grafana 未缩容**，仍是 `1/1 Running`（缩为 0 只省一个 pod 的内存，收益很小，不值得动一个已验证通过的栈）；
   - 如果以后要直接看集群内的 Grafana，两条可用路径：dmdb 走 `PermitOpen` + `ssh -L`；datax 只能在外部主机上再起一个绑非回环地址的 `port-forward`（Grafana 有登录认证，比暴露 Loki 安全得多）。
3. **本方案不替代**集群内 Grafana 的自愈能力：外部 Grafana 一旦长时间不可用，日志仍在 Loki 里，恢复后可直接查（`port-forward` 只是读取通道，不参与写入）。

## 相关文档

- 日志栈本身（Alloy + Loki + Grafana 的部署与五项验收）：[observability-logging-stack-deployment.md](./observability-logging-stack-deployment.md)
- 组件清单与边界：[`k8s/local-pvc/observability/README.md`](../k8s/local-pvc/observability/README.md)
- 外部接入清单：[`k8s/local-pvc/observability/external-access/`](../k8s/local-pvc/observability/external-access/)
