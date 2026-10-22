# WeKnora 有限业务文件试拷贝（不是全量迁移）

仅在 datax-03 运行。用户已批准最多5000个文件、累计不超过1GiB、限速10MiB/s，
复制到已经测试过的内网 NFS 新试验目录。保留本地原数据，不停任务、不改生产
挂载、不修改 NFS 导出、PV/PVC、并发、模型或副本数。不读取数据库或调用模型。

## 执行

将交付的 `weknora-rwx-copy-pilot-20261017-r1.py` 上传到 datax-03 的
`/home/cnooc/`，执行 `RUN-DATAX03.txt` 整段命令。
启动器校验脚本哈希，工具将自己固定到新的 root 私有状态目录：
`/var/lib/weknora-rwx-copy-pilot-20261017-r1/pilot.py`。
启动 transient systemd 单元 `weknora-rwx-copy-pilot-20261017-r1.service`，Restart=no。
启动后立即返回，不代表复制已经成功；关闭终端不会主动取消此后台单元。

约1分钟后执行（可重复，是只读查询）：

```bash
sudo python3 -I -B /var/lib/weknora-rwx-copy-pilot-20261017-r1/pilot.py --status
```

发送其输出。若还在运行，隔1分钟再查；不要重新执行 `--start-once`，不要删除状态、
清理试验目录或重启单元。只有 RESULT.json 中 pilot=PASS 才是本轮采样复制通过。
FAILED/UNCONFIRMED/无最终回执都需要复核，不能据此启动全量迁移。
只读状态读取本地回执及 systemd 状态，不挂载、不读业务文件、不发起复制。

## 前置证据与实际检查

- 读取 datax-03 原采集结果并核对固定 SHA256：
  `5220a8c5b3829bc65b93c4c0bb76a2be8270d0f449a9e78aaae52a61353eb68d`。
- datax-01 的清单与跨节点合成测试回执哈希作为用户提供的历史证据保留；不冒充
  本轮新的集群查询。本工具不需要 datax-03 的 kubeconfig，也不复制 kubeconfig。
- 新鲜检查宿主机内存至少8GiB、load1不超过逻辑CPU数的1.5倍；NFS余量至少100GiB、
  可用inode至少10万。限制只适用于本次1GiB试验，不是生产容量保证。
- 目标导出必须为空，且根目录仍是1000:1000/0770。发现已有文件立即停止，不删除。

## 隔离和复制行为

工具只在自己的私有 mount namespace 内建立源只读 bind mount 和临时 NFS 挂载；
核实与宿主 PID1 的 mount namespace 不同、传播不是 shared/slave，源挂载确为RO。
不修改宿主机已有挂载或 /etc/fstab。NFS 使用已验证的固定路径：
`192.168.111.189:/mnt/data/weknora-rwx-r2/files`，保持v4.1、hard、TCP、nodev/nosuid/noexec。
root_squash 不变。父进程用 O_PATH 持有 NFS 根，子进程清空附加组并降到UID/GID1000
后才取得实际读写目录描述符，避免 root_squash 下 root 无权读取0770根目录的问题。

采用标准库描述符相对复制，不启动 rsync daemon，不安装软件。这里不是对现有
rsync 3.1.3 的性能或安全回补验收。限定采样需要严格控制源路径竞态与逐文件字节上限；
后续全量同步工具另行评审，不把本轮速度直接当作 rsync 全量速度。

最多扫描180秒/150万条目，跨设备/子挂载拒绝、链接不跟随。只选择至少5分钟未修改、
1000:1000/0644、单硬链接的普通文件，按大小分层蓄水池采样；超过64MiB的文件不参与。
扫描可记录其他文件变化；每个入选文件在实际复制前、复制后和读回校验后均检查
dev/inode/size/mtime/ctime/owner/mode/link-count。变化则本轮不确认，不自动重试。

独立目标为 `/mnt/data/weknora-rwx-r2/files/.weknora-copy-pilot-<随机标识>/`，
仅新建0700目录、0600文件，逐个 fsync 并SHA256读回。不会修改源权限、写源内容、
覆盖目标已有文件、删除任何数据。源以RO绑定且读取使用O_NOATIME。
不读取或复制 DocReader 临时目录，本轮只试验知识库文件目录。

复制写入和验证读回的**合计负载字节**按10MiB/s节流；元数据请求不受字节限速精确约束，
但文件数受5000上限约束。最多写1GiB、读回1GiB，源读取最多1GiB另加EOF检查。
SHA256逐文件核对，输出只含聚合计数、大小档、速度和随机试验目录，不含业务文件名、
内容或原始错误。试拷贝结果保留占用不超过1GiB加目录/文件系统开销，不自动删除。

## 如何解释结果

PASS仅证明这批样本可由UID1000读取、在目标创建并同客户端校验。文件速度和逻辑字节
速度包括限速、SHA256、fsync和读回开销；读回可能命中客户端缓存。它既不是未限速峰值，
也不是完整迁移或 NFS 服务端掉电持久性/跨节点读回验收。ACL/xattr/原权限和时间戳不在
本轮保真范围，最终迁移需单独确定。扫描运行在活跃源目录，不是同一时间点快照。

复制/校验在操作之间检查20分钟截止；扫描单独180秒上限。NFS hard 挂载遇到服务器
故障时内核I/O可能等待超过这些时间，工具不会改为soft或强制卸载。状态会保留上次
进度和父进程心跳；出现长时间不动请发回状态，不要重复启动。
私有挂载随单元命名空间退出释放，不发出影响生产的卸载命令。

本地离线测试使用合成内容和真实Linux UID切换。systemd、mount隔离及真实NFS执行仍需
本次服务器回执确认。下一步是评审在线全量预复制；最终切换仍需控制所有写入口、完成
已领取任务、最终差异核对。先验收单副本共享存储，再按总并发预算准备跨节点双副本。

参考：
[Linux unshare 手册](https://man7.org/linux/man-pages/man1/unshare.1.html)、
[Linux NFS 手册](https://man7.org/linux/man-pages/man5/nfs.5.html)。
