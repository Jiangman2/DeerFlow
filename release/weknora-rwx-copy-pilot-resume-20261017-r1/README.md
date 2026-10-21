# 有限试拷贝启动恢复：systemd 243 零秒超时

## 已确认原因

datax-03 的原临时单元实际为 Type=oneshot、TimeoutStartUSec=0；2026-10-17
18:56:40 的 journal 在同一秒记录启动、启动超时、SIGTERM。ExecMainCode=2 /
ExecMainStatus=15，MainPID/ControlPID 均为0。原 STARTED 回执和脚本已固定哈希，
没有 RUN-ENTERED、挂载或复制标记。本轮仍须重新核对这些证据和旧单元进程缺席。

v243 的 systemd-run 临时属性通道对 TimeoutStartSec 使用 parse_sec，而非将0换成
无穷的 parse_sec_fix_0；不能套用普通 unit 文件中“0代表禁用”的假设。此外，
Type=oneshot 的启动任务等待整个命令完成，与原启动器20秒命令等待不匹配。

参考上游固定版本源码：
- https://github.com/systemd/systemd/blob/v243/src/shared/bus-unit-util.c#L197-L215
- https://github.com/systemd/systemd/blob/v243/src/basic/time-util.c#L945-L960
- https://github.com/systemd/systemd/blob/v243/src/core/service.c#L3346-L3358
- https://github.com/systemd/systemd/blob/v243/man/systemd-run.xml#L307-L324

## 执行方式

仅上传 `weknora-rwx-copy-pilot-resume-20261017-r1.py` 到 datax-03 的
`/home/cnooc/`，再执行本包 `RUN-DATAX03.txt` 完整命令。不要执行原 --start-once，
不要 reset-failed/restart 原单元，不删除旧状态或任何试验文件。

恢复工具先只读核对：原 STARTED SHA、脚本 SHA、token、START-REQUESTED 单元身份、
原状态目录仅三个预期文件、精确的已失败单元属性和 ExecStart；扫描 /proc/*/cgroup
确认没有旧单元进程，检查宿主及当前 mountinfo 没有旧状态目录挂载。两次稳定采样
后才创建独立新状态；任何不符均停止，不自动修正、不自动再试。

原脚本逐字节保持不变。复制到新状态后，以新模块实例加载并仅绑定新的状态/单元及
脱敏命令诊断；不会调用旧 start()。新单元使用 Type=exec、TimeoutStartSec=30s、
Restart=no；启动等待最多45秒，不等业务复制完成。工作入口再次核对旧失败边界、
自身哈希与实际生效的单元属性、MainPID 和私有挂载命名空间，之后才调用复制引擎。

新状态：`/var/lib/weknora-rwx-copy-pilot-resume-20261017-r1`
新单元：`weknora-rwx-copy-pilot-resume-20261017-r1.service`

启动确认只表示后台进程已启动，**不代表拷贝通过**。启动约1分钟后只读查询：

```bash
sudo python3 -I -B /var/lib/weknora-rwx-copy-pilot-resume-20261017-r1/resume.py --status
```

继续运行时可隔1分钟查询。只有 RESULT.json 中 `pilot=PASS` 才是这次样本校验完成。
没有结果、失败或超时均发送状态，不重新运行恢复命令。successful transient unit
完成后可能从 systemd 卸载；状态以通过安全读取、能够完整解析的最终回执为准，
单元“not-found”本身不能证明成功。回执文件若不完整会失败，不据此重放。

## 范围不变

- 最多5000个文件、累计1GiB；写入与验证读回合计10MiB/s。
- 独立 NFS 试验子目录，目标导出必须仍为空；不覆盖、不删除。
- 原 source 只读 bind、私有 mount namespace、NFS v4.1 hard/root_squash。
- 业务读取和目标写入为 UID/GID1000；逐文件稳定性检测、fsync、SHA256读回。
- 不修改生产挂载、NFS导出、PV/PVC、镜像、数据库、并发、模型或副本数。
- 不搬全量250GiB，不清理原数据/旧单元/既有回执；不是双副本上线。

保留原引擎180秒扫描和20分钟操作间截止。30秒仅为新单元**启动**等待，不是复制期限。
hard NFS I/O 故障可能超过程序截止；RuntimeMaxSec=infinity 避免把启动修复变成对正在
写入文件的自动强杀。没有定时强杀、soft挂载或强制卸载兜底；长时间无进展时应审查。
断开启动终端不主动取消 systemd 后台单元。

本地资格测试包括真实 Linux UID1000 合成文件复制及失败边界测试；systemd243、真实
私有挂载与企业 NFS 集成仍需服务器回执。通过不代表峰值吞吐、全量一致性、掉电持久性
或多副本可上线。下一步仍是评审在线预复制，再准备停写/最终差异校验/受控切换。
