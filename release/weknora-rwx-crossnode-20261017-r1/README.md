# WeKnora 现有 NFS：两节点合成读写验收

仅在 datax-01 执行；不在 NFS 服务端执行。先上传交付目录中的
`weknora-rwx-crossnode-20261017-r1.py` 到 `/home/cnooc/`，再完整执行
`RUN-DATAX01.txt`。不要逐行复制带 `>` 的终端回显。

本工具绑定用户返回的 NFS r2 成功回执 SHA256
`72f8c073b339dc84f4746a76d494014288f38f7bb02654a5c8697f67f31a499f`。
其字节由完整回执重构并校验；这不是新的服务端状态读取。当前可访问性
由接下来的真实 NFS 挂载与读写验证。

## 本轮范围

- 新建两组静态 Retain / ReadWriteMany PV/PVC，命名为
  `deerflow-weknora-files-pv-nfs-r2` / `weknora-data-files-rwx-r2` 和
  `deerflow-weknora-docreader-pv-nfs-r2` / `weknora-docreader-tmp-rwx-r2`。
- 路径仅为 `192.168.111.189:/mnt/data/weknora-rwx-r2/{files,docreader}`。
  限定 datax-01 / datax-03，NFSv4.1、hard mount，不改变服务端 root_squash。
- PV 声明 400Gi / 32Gi 只是 Kubernetes 容量元数据，不是磁盘预留、配额或扩容承诺。
- 在 deer-flow 命名空间创建两个临时 Pod 和仅选择这些 Pod 的 deny-all
  NetworkPolicy。使用已预载的精确候选镜像，覆盖入口为 Python 等待进程，
  不启动 WeKnora，不读取生产 Secret/ConfigMap，不挂生产卷，不访问数据库/Redis/模型。
- UID/GID1000，禁止特权提升、只读容器根目录，不设置 fsGroup，避免递归改权限。
  默认服务账号不自动挂载令牌。所有新对象先 server dry-run，再检查准入后的实际规格。
- datax-01 写入并 fsync/rename；datax-03 校验后反向写入；datax-01 再校验。
  两卷共写入 256KiB，无真实文档。客户端 fsync 成功不是服务器断电持久性证明。
- 成功后仅删除校验过的四个合成文件、两个专用子目录、两个测试 Pod 和测试策略。
  删除 API 带 UID 前置条件；保留两组新 PV/PVC，不删除任何业务数据。

## 输出与中断

执行前打印只读 `status_command`。正常通常数分钟；节点挂载或调度失败会有等待输出。
NFS hard mount 的内核 I/O 故障可能超出客户端超时；工具不会强制卸载或强杀宿主进程。
测试 Pod 设置 600 秒 activeDeadline，但不能保证故障中的内核挂载立即退出。

若失败、Ctrl+C、SSH 中断或输出不明，**只执行打印出的 `--status`**。
不要重跑 `--verify-once`，不要删除状态目录或重建同名对象。
已创建但未收到 ACK 的对象也会在状态中按确切名称列出，交由下一步核实。
失败不自动删除对象或重做写入；原始回执不可覆盖。

`cross_node_io=PASS` 仅证明本次两节点 UID1000 小文件交叉可读写、改名及校验成功。
NetworkPolicy 的独立强制执行、满载吞吐、锁竞争、服务器故障恢复都未验收。
本地测试使用模拟 Kubernetes API / mountinfo，另有真实 Linux UID1000 文件操作，
不能冒充真实跨节点 NFS 测试。

## 明确不做

不复制约250GiB历史知识库；不切生产 PVC；不重启生产 WeKnora/DocReader；
不改104槽位、MinerU16、模型、队列或数据库；不创建第二个生产 App 副本。
PASS 后仍 `ready_to_migrate=false`、`ready_to_scale=false`，下一门槛为迁移计划和
有界复制试验评审。先保留 datax-03 原始数据；后续离线切换要验证增量追平、
读写暂停边界、清单一致性、回退条件，再谈双副本与集群总并发预算。

参考：Kubernetes 的 [持久卷/访问模式](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
与 [Pod 安全上下文](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/)。
RWX 标签本身不等于实际共享存储验收，应用双副本也不会消除单台 NFS 的故障点。
