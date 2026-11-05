# 高危端口开放整改脚本

适用范围：报告涉及的 Linux/Kylin 主机。脚本默认只读盘点；`apply` 使用独立的 nftables 表限制指定端口的来源，并先启动自动回滚计时器。必须在每台服务器分别核对策略，先选一台验证。Python 3.6+、nftables 和正常运行的 systemd 是应用规则的前提；缺失时脚本退出，不自动安装软件。

脚本：`scripts/guard_high_risk_ports.py`。模板：`scripts/high-risk-ports.policy.example.json`。

## 1. 上传与盘点

将脚本和模板上传到服务器自己的工作目录，运行：

```bash
sudo python3 guard_high_risk_ports.py audit > port-audit.txt 2>&1
cp high-risk-ports.policy.example.json ports-policy.json
```

盘点包含监听端口、网卡、路由、RPC/NFS、防火墙以及 Kubernetes Service/Node 清单。没有权限、缺少工具或 Kubernetes 配置时，对应项不是通过结论。必要时使用有集群权限的账户另行执行 `kubectl get svc -A -o wide` 和 `kubectl get nodes -o wide`。盘点文件含网络信息，应按内部运维资料保存。

## 2. 填写本机策略

编辑 `ports-policy.json`，替换所有 `REPLACE_*`，核对完成后将 `reviewed` 改为 `true`。默认模板不能直接应用。

| 字段/服务 | 需要填写的实际值 |
| --- | --- |
| `interfaces` | 需要限制外部访问的本机入站网卡，如 `ens192`；多网卡逐一列出 |
| `ssh` | SSH 实际端口、服务器看到的堡垒机/管理来源 IP；单 IP 用 `/32`，IPv6 用 `/128` |
| `https` | 访问 443 的业务网段；如另有必要的 HTTP 入口，单独增加 80 的服务项 |
| `deerflow_nodeport` | 32026 的反向代理实际来源；若用户直接访问这个端口，需要列出用户业务来源 |
| `cluster` | 本机所需控制面、所有集群节点及真实 Pod 来源；核实 2379/2380/6443/10250 的访问方向 |
| `monitoring` | 9100/10256 的真实监控、节点或健康检查来源 |
| `nfs_tcp` / `nfs_udp` | NFS 客户端 IP，以及 `rpcinfo -p` 中的实际端口和协议；模板中的动态端口仅取自初测报告 |
| `other` | 135/9090/38001/38002/38003 先确认进程和用途；`sources: []` 表示全部拒绝，保留使用时必须填写来源 |

同一组端口共用相同允许来源；需要不同权限时拆成多个服务项。每个端口/协议只能出现一次。允许来源支持 IPv4/IPv6 地址和规范 CIDR，不接受 `/0`。只有 IPv4 允许来源时，相应 IPv6 入站也会被拒绝。

不要凭端口号认定服务。特别是动态 RPC/NFS、健康检查、外部负载均衡、NAT 后来源以及非默认 SSH 端口，必须以本机实际证据为准。新增不在策略内的端口不受此脚本限制，仍需纳入后续盘点与复测。

## 3. 预览与应用

```bash
python3 guard_high_risk_ports.py plan --policy ports-policy.json > port-plan.nft
sudo --preserve-env=SSH_CONNECTION python3 guard_high_risk_ports.py apply --policy ports-policy.json --rollback-minutes 10
```

`plan` 只生成规则。`apply` 会检查当前 SSH 来源和服务端口、网卡、已有独立规则表，保存原 nftables 规则快照，再用 `nft -c` 检查目标主机是否支持生成的语法；计时器启动成功后才加载规则。整个 nft 规则批次一次性加载。

若由真实控制台登录而没有 `SSH_CONNECTION`，可明确使用 `--console-access`。不要在远程会话中为了跳过 SSH 检查使用此参数。保留原 SSH 会话并立即从另一个会话重连。

执行结果会打印本次状态目录 `/var/lib/deerflow-port-guard/<编号>`、确认命令和回滚命令。默认 10 分钟未确认会自动删除本次独立规则表。

## 4. 从其他机器验证，然后确认

至少分别从允许来源和不允许来源复测，服务器本机自测不能证明网络隔离：

```bash
# 在已授权的复测机器上执行，替换服务器地址
nmap -Pn -sT -p 22,135,443,111,1297,2049,2379,2380,6443,8655,9090,9100,10250,10256,20048,32026,38001,38002,38003 SERVER_IP
# 涉及 UDP RPC/NFS 时，另从授权机器验证对应 UDP 端口
sudo nmap -Pn -sU -p 111,1297,2049,8655,20048 SERVER_IP
```

拒绝来源应无法访问受限端口；`filtered` 是 DROP 的常见结果，UDP `open|filtered` 单独不足以证明关闭。允许来源还须实际验证：新 SSH 登录、网站登录与对话/文件操作、节点 Ready、现有 NFS 文件读写、监控采集和负载均衡健康检查。使用 IPv6 时也要分别复测。防火墙原有规则仍可能阻止允许来源，脚本的允许项不会覆盖原来的拒绝规则。

全部正常后，在计时器到期之前执行脚本打印的确认命令，例如：

```bash
sudo python3 /var/lib/deerflow-port-guard/实际编号/guard.py confirm --state /var/lib/deerflow-port-guard/实际编号 --verified
```

确认只取消自动回滚，并保留当前运行时规则。`--verified` 表示操作者已完成外部验证，脚本不会自动判定业务正常。

发现异常可立即执行打印的回滚命令；SSH 已断开时等待自动回滚，或使用控制台：

```bash
sudo python3 /var/lib/deerflow-port-guard/实际编号/guard.py rollback --state /var/lib/deerflow-port-guard/实际编号
```

回滚只删除本次专用表，不恢复整份快照，因此不会覆盖执行期间 Kubernetes 或其他防火墙组件产生的规则。快照 `before.nft` 仅作诊断证据；不要直接加载以替换动态集群规则。

## 5. 持久化与关闭整改项

这是带自动回滚的运行时验证脚本，**重启或防火墙重载后不保证规则仍存在**。通过验证后，以本次目录中的 `rules.nft` 为依据，由运维纳入服务器现有防火墙配置管理，并在重载、重启后复测；不要直接启用第二套会清空现有规则的防火墙服务。完成持久化和外部复测前，高危端口整改不能认定为完成。

对确认无用的监听进程，应另行停止并禁用对应服务、移除不必要的 Kubernetes NodePort，并在安全组/边界防火墙同步限制来源。脚本不会猜测进程用途并自动停用服务，也不改动 NFS 导出、集群转发或业务配置。

## 实现边界

专用 `inet deerflow_port_guard` 表在 `prerouting` 的优先级 `-150` 过滤指定入站网卡、目的地为本机地址的流量，覆盖 IPv4/IPv6，并在通常优先级 `-100` 的 DNAT 前检查原始 NodePort。不会清空现有规则，也不全局禁止其他端口或非本机转发流量。

如果集群使用 XDP/eBPF 提前转换或绕过常规 Netfilter，或者已有更早的 DNAT，不能仅凭该规则断言 NodePort 已隔离，应在上游防火墙或相应 CNI 策略实现限制并复测。系统不支持 FIB 表达式时，预检失败且不会应用规则。

参考：[nftables hook 优先级](https://wiki.nftables.org/wiki-nftables/index.php/Netfilter_hooks)、[路由信息匹配](https://wiki.nftables.org/wiki-nftables/index.php/Matching_routing_information)、[systemd 临时计时器](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html)。
