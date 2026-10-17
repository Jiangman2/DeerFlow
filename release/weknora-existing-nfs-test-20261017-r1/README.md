# 现有 NFS 验证：第一步，新建独立目录与精确导出

2026-10-17 用户重新选择优先复用现有 NFS，加快跨节点双副本准备。
本包是新版本，不启用之前已停用的 r1 草稿，也不要求运行 datax-03 NFS 审计。

## 本次仅执行

在 **ecs-zdz-pro-0000 / 192.168.111.189** 上：

- 创建 `/mnt/data/weknora-rwx-r2`，root:root 0755；
- 创建其下 `files` 与 `docreader`，UID:GID 1000:1000，0770；
- 新建 `/etc/exports.d/weknora-rwx-20261017-r2.exports`；
- 两个目录仅向 `192.168.111.2`、`192.168.111.18` 两个已核实节点导出；
- 使用 `rw,sync,root_squash,no_all_squash,no_subtree_check,secure,sec=sys`；
- 逐个精确 host:path 添加，不执行 exportfs -ra/-a/-u，不重启 NFS。

旧 home/skills 目录和导出保持不变；新目录位于独立路径，无需改变旧父目录
0750 权限。本次不复制约 250 GiB 业务文件、不创建生产挂载、不增加副本。
没有数据库/Redis/模型请求，没有 Kubernetes 操作。

## 执行

上传交付脚本 `weknora-existing-nfs-stage-20261017-r1.py` 到 **NFS 节点**
`/home/cnooc/`。执行完整 `RUN-NFS.txt`。不要在 datax-01/03 运行此步骤。
启动器校验脚本哈希，打印只读状态命令，并保存新的 root 私有日志。

脚本先只读检查服务地址、XFS 挂载、空闲容量、目录安全、活动导出和持久配置，
然后记录一次性操作意图，再创建和校验。小文件测试阶段要求至少 10 GiB 与
10 万空闲 inode；这不是将来迁移 250 GiB 的容量放行或磁盘配额。

成功标识：`nfs_directory_export_stage=PASS`。发送最终 JSON 和两个退出码。
遇到失败/断线/Ctrl+C，只执行日志打印的 `--status`；不重复创建、不删状态、
不手工 unexport、不 chmod 777。工具保留已完成的精确步骤供后续核对。
`--check-only` 与 `--status` 都不写文件、不重新导出。

## 后续顺序（本包不会执行）

1. 根据 NFS 创建回执创建新 Retain PV/PVC 与独立测试 Pod；不覆盖旧 RWO claim。
2. datax-01/datax-03 以 UID 1000 验证 NFSv4.1、写入、原子改名、跨节点读回、
   校验和与小文件操作；测试不读企业文档、不调用 WeKnora/模型。
3. 若功能与有限吞吐测试达标，另行准备全量预复制、停止相关写入后的最终增量
   同步/校验、单副本换卷验收和回滚。这里没有授权开始全量复制。
4. 最后才做跨节点双副本，按总容量分配 Worker 槽位并检查 DB/Redis预算。

NFS 导出创建成功不是跨节点验证成功；小文件通过也不证明 250 GiB 迁移耗时、
长期吞吐或高可用。NFS 节点仍为存储单点。AUTH_SYS 信任授权客户端主机的
数字 UID，不提供加密或不可信客户端之间的强隔离。

## 本地测试与边界

package.py 在无网络的 Linux 容器中执行合成测试、Python 3.7 语法、Bash 语法
检查，并仅生成新交付目录。测试真实执行临时目录/权限/回执操作，但 exportfs
用内存桩模拟，不调用生产主机或真正修改任何主机导出。

导出语义依据：https://www.man7.org/linux/man-pages/man8/exportfs.8.html
