# PPT 中继参数修复：独立离线候选构建

这不是生产 rollout 包。当前模型为 DeepSeek-V4-Flash，不切换模型。
从已交付 R1 候选包冻结一个 Gateway 中继文件，保留 channel-pdf-preview-r2
镜像的其余功能、启动配置和已有层。不包含数字员工改造，不更新沙箱、
Provisioner、Runner、数据库、Skill 或任何 Kubernetes 资源。

## 构建边界

- 仅在 ARM64 datax-01 上构建；Gateway 必须保持在 ecs-zdz-pro-0022。
- 使用已存在的本地 OCI 归档，不自动导出、不拉镜像、不安装依赖。
- 独立 BuildKit 数据目录位于新的 /opt 子目录，/opt 必须独立挂载。
- 独立网络命名空间仅有 loopback，所有 RUN 均 network=none。
- CPU 150%、内存 12 GiB、并发 1；/opt 读 80 MiB/s、写 40 MiB/s 总预算，
  daemon、客户端和 RUN 叶子各限制到三分之一，先验证内核控制器及真实 RUN。
- cgroup v1 对 buffered writeback 的归属有内核限制，这不是绝对业务性能保证；
  同一宿主的其他业务仍可能共享磁盘和内存带宽。只读检查通过不代表资源实测通过。
- 根分区必须低于 80% 且余量至少 30 GiB；/opt 余量至少 80 GiB；启动前可用
  内存至少 16 GiB，运行时保留至少 4 GiB。检查失败就停止，不修改节点全局设置。
- 中断时仅停止本次创建的构建单元；保留证据和部分文件，不删除缓存或生产镜像。

## 手动执行顺序

上传 tar.gz 和 sha256 至 datax-01:/home/cnooc，先验证外层 checksum，
解压到一个不存在的 /opt/deerflow-hotfixes 同名目录，再验证 PACKAGE.SHA256。
必须使用主机 Python 的 `-B` 参数避免往不可变包写入 __pycache__。

先只读检查（从包目录执行）：

```bash
python3 -B -u ./build-candidate.py \
  --output /opt/deerflow-build-artifacts/ppt-relay-r1-build1 \
  --check-only
```

默认基线归档是
`/opt/deerflow-build-artifacts/20260923-channel-pdf-preview-r2/gateway.candidate.oci.tar`。
若已迁移，可通过 `--base-archive /opt/.../gateway.candidate.oci.tar` 指定。
只读检查只检查归档存在/权限；真正构建前会离线验证完整 OCI 图、所有 blob 哈希、
ARM64 架构和审计运行镜像 ID，错误归档不能通过。

仅当检查 `ready_for_build=true` 后再执行：

```bash
python3 -B -u ./build-candidate.py \
  --output /opt/deerflow-build-artifacts/ppt-relay-r1-build1 \
  --build-only-ack YES
```

构建每阶段输出进度标记，详细日志保存在新输出目录内，root 私有。
不要并发运行旧 PPT 构建或导入。已有输出不能覆盖，失败重试用新的输出目录。
不要手动取消资源保护门禁，也不要将这个候选镜像直接 set image 到生产。

成功后提交 `BUILD-RESULT.json`。尚需隔离真实模型工具调用、普通对话/渠道/PDF
回归和回退检查，才能提供生产热更命令。修复不保证模型永远生成正确参数；
它保证非法参数先拦截、有限次纠正，不猜测命令、不把坏调用发送给执行端。
