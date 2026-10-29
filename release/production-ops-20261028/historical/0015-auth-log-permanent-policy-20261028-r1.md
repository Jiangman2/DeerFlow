# 0015 auth.log 永久轮转与数据盘归档

初次转存已通过操作员回执验收，见 `0015-auth-log-relocation-field-20261028-r1.json`。根盘实际释放 5,454,757,888 字节，可用增至 23,942,234,112 字节（约 22.30 GiB）。旧日志 5,456,342,972 字节已完整保留在数据盘，SHA256 为 `57610773cb324e17f5107d7f3858d87227520e18931f4706f28cd9a4254bbeea`。日志进程重新打开新文件，权限和 `security.kysec` 保留，服务重启与工作负载 patch 请求均为 0。

同日后续集群状态表显示所有报告的 Deployment、StatefulSet 和 DaemonSet 均达到期望数量；Gateway 70/70、Frontend 2/2、调度器 2/2、Alloy 4/4。datax-01、0015 均 Ready，DiskPressure 与 MemoryPressure 为 False。该检查不等同于每个沙箱和业务请求验收，摘要见 `post-auth-log-relocation-health-field-20261028.json`。

## 永久策略

- 利用已经运行的 crond，每小时第 17 分钟检查一次；仅 auth.log 达到 100 MiB 时轮转。
- 使用独立 logrotate 配置，未放入全局 `/etc/logrotate.d`，防止默认每日任务和专用任务重复轮转。
- 同文件系统 rename/create，保留权限和所有扩展属性；元数据恢复成功后才向原 rsyslog 主进程发送 HUP，确认新文件写入及旧文件关闭。
- 数据盘归档保留完整文件，16 MiB/s 复制、32 MiB/s 回读 SHA256，验证后才释放根盘旧副本；不配置历史归档自动删除。
- 进程锁防止并发运行；低于阈值不轮转、不发送信号。已有未完成周期先处理，不再叠加新的轮转。
- 完整复制后的校验中断，以及已删除根盘旧文件但尚未写完周期回执的中断，可通过原周期安全续接。截短或摘要不符的归档保留原件并返回非零，需人工核对，不覆盖证据。
- 数据盘未挂载、余量不足 20 GiB、安全属性不支持、配置改动或写入进程状态异常时停止。安装工具不重启服务，不立即运行维护，不访问容器运行时。

100 MiB 是每小时检查时的触发阈值，不是强制文件大小上限。该策略解决 auth.log 缺少轮转造成的持续增长；其他目录仍应按既有磁盘监控管理。

## 安装文件

| 路径 | 用途 |
| --- | --- |
| `/opt/portal-auth-log-policy-0015-20261028-r1/maintenance.py` | 固定版本维护程序，内嵌已验收转存逻辑 |
| `/opt/portal-auth-log-policy-0015-20261028-r1/INSTALL.json` | 程序、规则和 cron 的摘要 |
| `/etc/portal-auth-log-0015-20261028-r1.conf` | 独立 logrotate 配置 |
| `/etc/cron.d/portal-auth-log-0015` | 每小时检查入口 |
| `/var/lib/portal-auth-log-policy-0015-20261028-r1/` | 锁、周期状态、安装及最近运行报告 |
| `/var/log/portal-auth-log-pending-0015-20261028-r1/` | 尚未验证转存完成的旧日志 |
| `/mnt/data/portal-auth-log-archives-0015/` | 每个周期独立的完整归档、journal 和回执 |

专用目录以 root:root 0700 创建。安装绑定初次转存原报告的 1,013 字节及 SHA256 `3ba110937068b73d50991b60fe417197c37823af4d3370f9f13f12bd90f78d90`，保留初次归档目录。只有规则模拟校验及日志进程检查通过后，才发布 cron 文件。现有 rsyslog 和 crond 无需重启。

## 现场操作

只在 ecs-zdz-pro-0015 执行一次：

```bash
sudo python3 -I -B -u /home/cnooc/install-portal-0015-auth-log-policy-20261028-r1.py --install-auth-log-policy-once
```

成功应返回 `AUTH_LOG_PERMANENT_POLICY_INSTALLED_CHECKED_NO_ROTATION`，rotation_requests、service_signal_requests 和 service_restart_requests 均为 0。保留输出供验收；失败不重复安装。

安装后的固定程序支持 `--check-readonly`，只核验已安装摘要及模拟配置；`--maintain` 是 cron 的实际维护入口。普通巡检应读取 `LAST-REPORT.json`，无需手动强制维护或运行 logrotate -f。第一次计划执行结果仍需现场回执确认，安装成功不等同于已证明计划任务触发。

## 依据与本地验证

- [logrotate 官方手册](https://github.com/logrotate/logrotate/blob/main/logrotate.8.in)：大小触发条件与调度频率是两个独立设置。
- [crond 手册](https://www.man7.org/linux/man-pages/man8/crond.8.html)：监测 `/etc/cron.d` 变动并按计划执行，无需重启。
- [rsyslog 官方说明](https://docs.rsyslog.com/doc/reference/parameters/omfile-file.html)：外部轮转后以 HUP 重新打开文件。

12 项 Python 3.7 隔离测试覆盖真实 logrotate、跨文件系统转存、标签错误拦截、并发锁、阈值跳过、重复运行、中断恢复、部分副本保留、规则篡改拒绝与 cron 最后发布。实际 `security.kysec` 写入支持在每次轮转前用空文件复核，本机测试使用非敏感 user 属性模拟。

## 现场安装验收

操作员回执 `0015-auth-log-policy-install-field-20261028-r1.json` 确认状态为 `AUTH_LOG_PERMANENT_POLICY_INSTALLED_CHECKED_NO_ROTATION`。程序、配置和 cron 三个文件的摘要校验通过，日志进程仍为 PID 1204，当前 auth.log 为 1,559,634 字节，低于 104,857,600 字节阈值。安装期间轮转、服务信号、服务重启、工作负载 patch 和容器运行时操作请求均为 0。计划按每小时第 17 分钟运行，完整归档不自动删除。

此回执确认安装和模拟校验通过，尚未取得第一次计划任务实际执行的回执。后续应读取 `LAST-REPORT.json` 核对调度触发；不需要为验收而强制轮转未达阈值的日志。
