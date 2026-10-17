# datax-03 原盘共享：测试导出阶段

数据继续留在 datax-03 的3TB XFS盘。本阶段只新建空测试目录，验证采用
root管理的固定挂载入口后能否提供共享存储；不导出原知识库/DocReader目录。

已核实 /opt 是1000:1000/0755，其余父目录与业务根目录的权限符合现状。
因此这里不修改 /opt 权限，也不跳过所有目录检查。工具逐级以 O_NOFOLLOW
打开目录，核对操作员返回的设备号、inode、UID/GID和模式；新建测试目录后
持有其FD，直接以 mount(2) 绑定到 root管理的 /var/lib 入口，避免重新解析
UID1000管理的祖先路径。绑定入口和源目录指向同一数据盘inode，不复制数据。

## 本轮确切变更

- 新建 `/opt/deerflow-storage/weknora-nfs-test-20261017-r1`，1000:1000/0770。
- 新建 `/var/lib/weknora-datax03-nfs-export-20261017-r1`，root:root/0755。
- 将新测试目录绑定到其 `probe` 子目录，设置rw,nosuid,nodev,noexec及private传播。
- 新建一份 `/etc/exports.d/weknora-datax03-test-20261017-r1.exports`。
- 只新增两个精确主机导出，客户端为 datax-01/.2 与 datax-03/.18。
- 导出选项包含sync,root_squash,no_all_squash,secure,sec=sys,mountpoint。
- 新增root私有状态 `/var/lib/weknora-datax03-nfs-test-20261017-r1`。

NFS服务已运行；工具不安装软件、不启动/重启服务、不全局重载导出、不改防火墙。
原有配置和导出必须保持此前的空基线；发生外部变更时停止并保留证据。
既有知识库目录的归属、权限、数据、生产Pod挂载与104槽位均保持原状。

## 执行

将 `weknora-datax03-nfs-test-stage-20261017-r1.py` 上传到 datax-03 的
`/home/cnooc/`，完整执行 `RUN-DATAX03.txt` 一次。通常几十秒完成。
它绑定本次 `/var/lib/weknora-datax03-share-audit-dLRuiSI0/audit.log` 和原检查
脚本SHA，执行前再次核实服务、路径、空导出基线和本机地址。

成功应返回 `datax03_test_export_stage=PASS`、`exports_created=2`，以及回执SHA。
请发送最终JSON和退出码。下一轮按该回执创建独立测试卷与两节点UID1000探针，
验证写入、fsync、原子改名和交叉读回；通过后才准备真实目录固定入口。

状态命令仅只读：

    sudo python3 -I -B /var/lib/weknora-datax03-nfs-test-20261017-r1/stage.py --status

中断、失败或结果不明时只查状态。保留意图和部分资源，不重跑stage、不删状态、
不手工卸载或改权限；工具不自动重试或回滚。

## 结果含义

PASS只代表本机新增测试导出和绑定验证完成，跨节点I/O尚待验收。
该测试绑定挂载不配置重启持久化；mountpoint导出选项要求挂载存在，防止重启后
意外导出其底层空目录。生产接入阶段另行配置带身份校验的持久化挂载和启动顺序。
AUTH_SYS信任指定客户端主机的数字UID，不提供传输加密。NFS仍依赖datax-03。
不复制250GiB，不改变数据后端，不做数据库迁移，不创建第二个生产App副本。

离线测试使用真实Linux目录FD和UID/权限操作；mount/exportfs和服务状态使用模拟。
源码及原样交付件分别在Python3.7/3.12运行。它们不代表ARM4.19上的真实NFS验收。
另在隔离容器的临时tmpfs上执行真实mount(2)绑定、标志设置及inode核对，测试通过后
只卸载该容器自己的测试挂载；该结果不代表XFS/NFS服务已在服务器上验收。

参考：
https://man7.org/linux/man-pages/man5/exports.5.html
https://man7.org/linux/man-pages/man8/exportfs.8.html
https://man7.org/linux/man-pages/man2/mount.2.html
