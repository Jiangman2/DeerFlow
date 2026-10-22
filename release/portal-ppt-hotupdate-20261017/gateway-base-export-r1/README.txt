固定 Gateway 基底导出工具（2026-10-17，尚未在生产执行）

仅在 ecs-zdz-pro-0020 运行，Python 3.7+ 标准库。执行入口：
  sudo python3 -I -B -u /home/cnooc/export_gateway_base.py --export
实际交付文件名以主任务提供的命令为准。固定 socket=/run/containerd/containerd.sock，namespace=k8s.io。

锁定 tag：deerflow.local/deer-flow-gateway:20261017-background-stream-953eb97e4aaf
锁定 OCI manifest：sha256:ef500864eb402d0b9a76556764dc0e2a9d3fe417f6fef89fbf4e3816ee0c1c84
锁定 config：sha256:dff2b9d7323329733f14133b384030d6efeda948a86f082f055f1573ced224fd
固定 linux/arm64、29 个 layer descriptor；重复 descriptor 共用同一 blob，不错误要求重复归档成员。

仅执行 ctr images ls、两个固定 digest 的 content get，以及固定 tag 的 images export --platform linux/arm64。
export flags 取自本仓库已有 channel-pdf-preview-r1/build-candidate-datax-01.sh:50 和 capacity-wave1-r1/prepare-wave1.py:380。
不 pull/import/tag、不启动镜像、不操作 Kubernetes 或数据库、不构建、不检查应用源码。

在 root-owned /var/lib 下创建唯一随机 0700 目录；任何旧目录/文件均不覆写。
先验证当前 tag/manifest/config/platform 和磁盘余量（2 倍内容大小 + 512 MiB）。
总时间界限 1200 秒，export 至多 900 秒；输出实际持续 drain、有字节上限，导出文件有大小监控。
失败保留 root-private REPORT.json 与可能的 .partial，不自动重试；收到失败先核对报告。

归档只流式读取，不解包。验证 tar framing/checksum、唯一安全路径、全部预期 blob hash/size、OCI layout/index 和整包 SHA。
拒绝符号链接/硬链接/稀疏文件、PAX/扩展 header、未知成员、重复成员、路径穿越、损坏/缺失 blob 及非零隐藏尾部。
可选 Docker manifest.json 必须只包含锁定 config/tag/完整顺序 layer 引用；这用于兼容已用 ctr export flags。
合法但未覆盖的扩展格式将失败保留，必须人工审阅，绝不自动换导出方式。
不解压 layer 来重新计算 diff_ids；报告不声称完成该额外验证。

成功后生成 gateway.base.oci.tar 与安全 REPORT.json，所有写入/fsync/验证完成后，以已打开的 fd 核验 regular+nlink1。
先将两个文件转交固定系统账户 cnooc（0600），最后转交目录（0700）；父目录 /var/lib 仍属 root。
交接开始后不再执行任何 root 文件写入；交接结果仅在最终 stdout 报告，不回写 REPORT.json。
最终 stdout 附 REPORT.json 的路径、精确 SHA 和字节数。原始 OCI 含 image config，保持私有，供后续服务器构建使用，不上传聊天。
仅需返回脱敏 stdout/REPORT；本工具成功仍不代表源码预条件、候选构建、迁移或发布资格。

本地资格：Windows 11 项（8 通过、3 POSIX 专属跳过）；Linux 11/11 通过。
Linux 使用已有本地 QA 镜像，禁止拉取、无网络、只读根文件系统/工具目录、1 CPU/512 MiB，临时夹具只在 tmpfs。
具体记录见 LOCAL-VERIFICATION.json / LOCAL-VERIFICATION.log。
