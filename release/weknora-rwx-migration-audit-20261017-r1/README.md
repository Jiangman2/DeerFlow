# WeKnora NFS 迁移预检（不复制、不切换）

前置证据：2026-10-17 跨节点 NFS UID1000 合成读写 PASS，回执 SHA256
`8384148615788cfee1a8db011921e19de5dc752bdb32479bfab8cbf04495f756`。
datax-03 已确认 rsync3.1.3、setpriv、mount.nfs、findmnt 可用，两个源根目录
均1000:1000/0770。这不能代替子文件权限、文件类型及迁移写入方检查。

将同一 `weknora-rwx-migration-audit-20261017-r1.py` 分别上传到 datax-01、datax-03
的 `/home/cnooc/`，对应执行 RUN-DATAX01.txt、RUN-DATAX03.txt。两台可并行运行。
只能在对应主机以 root 运行；启动命令校验哈希并复制到新的 root 私有工具目录。

## datax-01：集群绑定与潜在写入方

校验原跨节点 RESULT 文件哈希与所创建 PV/PVC 的 UID；重新读取源/目标绑定。
只用 kubectl GET，扫描集群 PV、Pod、Deployment、StatefulSet、DaemonSet、Job、
CronJob 的相关卷引用，包括另一个 PVC 的同路径别名、hostPath 父目录、直接 NFS、
init/ephemeral 容器、零副本/暂停的控制器模板。两次采样发现变化则标记待复核。
源路径别名采取保守匹配，其他节点同名本地路径可能被列为待核实项，不等于它实际
访问了 datax-03 的文件。

输出的是**声明了相关挂载的使用方及只读标记**，不是已经观测到的写入行为。
不能排除宿主机脚本、外部程序或受特权进程控制的额外挂载，也不能证明停写。
现有配置/环境变量只在内存参与指纹计算，不输出、保存 API 原文或读取 Secret。

## datax-03：源目录元数据

仅扫描 `/opt/deerflow-storage/weknora-files`、`weknora-docreader` 的元数据。
不读文件内容，不输出/保存业务文件名，不执行 rsync 复制。目录使用 no-follow
文件描述符和 O_NOATIME；链接不跟随，跨设备/子挂载（含同设备 bind mount）跳过。
降低本采集进程优先级，每个根目录最多120秒或150万条目，15秒输出一次进度。
时间限制在元数据操作之间检查，异常内核 I/O 不能保证立即返回。

统计文件数、表观大小、分配块、尺寸档位、不同属主、POSIX 权限、硬链接、
稀疏文件、符号链接和特殊文件。硬链接可能重复计入字节；ACL/xattr 未检查；
POSIX 位估计不是实际 UID1000 打开文件成功的证明。扫描过程中仍有业务写入，
因此不是一致性快照，即使 traversal=COMPLETE 也不是最终迁移清单。
遇到时间/条目上限、读取错误、子挂载或挂载表变化明确标记 INCOMPLETE，不报零成功。

只读查询 rsync 可执行程序版本及发行版 RPM 包标识。旧版本号不能单独判定有无
安全回补；`vendor_security_backport_status=NOT_DETERMINED`，本工具不升级软件，
不启动 daemon，不连接外部 rsync 服务。后续复制会按实际版本选择参数。
参考：[rsync 官方手册](https://download.samba.org/pub/rsync/rsync.1)、
[官方安全公告](https://rsync.samba.org/security.html)。

## 输出与边界

结果直接打印，并存入各节点 `/var/lib/weknora-rwx-migration-audit-*/RESULT.json`。
另有带哈希的回执和只读 status_command（只读已存结果，不重新查询集群）。
请返回两节点结果；若 INCOMPLETE，不要自行去掉限制，也不要启动复制。

除了私有诊断日志/回执，本轮不创建或修改文件；不挂载、chmod/chown、复制、删除、
安装、停任务、读数据库/Redis或调用模型；不修改 NFS 导出、PV/PVC、模型或并发。
所有 ready_to_copy / ready_to_migrate / ready_to_scale 均保持 false。
本地单元测试使用离线 Linux 文件夹和模拟 API，不是生产采集结果。

下一步按结果限定小批量、限速的复制试验，再评审在线预复制。切换生产卷前仍需
控制全部写入入口、等待已领取任务结束、最终增量及校验；旧 datax-03 文件保留。
先验收单副本使用 NFS，再按集群总并发预算准备跨节点双副本，不直接翻倍 Worker。
