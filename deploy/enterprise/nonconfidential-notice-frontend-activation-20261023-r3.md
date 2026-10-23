# 红字前端切换 r3：验证节点本地执行能力

用户贴回 r2 失败：`new_image_not_observed_in_kubelet_cache`。当时 `patch_request_count=0`、`patch_requested=false`、`patch_may_have_applied=false`、`once_claimed=false`，没有修改前端。

原报告保留：`/var/lib/portal-frontend-nonconfidential-notice-activation-28a6a57592b045b4b82b6ad994d28e15/REPORT.json`；5,200 字节，SHA256 `00feb27a01a5d69462ba7ad9a77205f3cb3c4a658bccebd70ae864ef8e250e3c`。此摘要来自用户提供的回执，r3 会在服务器读取原完整报告、核对摘要和无写入状态。

原 node_gate 把 Node.Status.Images 当成了完整 CRI 清单。Kubelet 配置允许限制该列表，默认上限 50；不能从“未列出”证明镜像不存在。本次未采集实际节点的该配置，因此不宣称已证实现场是因截断导致。

参考：https://kubernetes.io/docs/reference/command-line-tools-reference/kubelet/#options （node-status-max-images）。

## 修正

- 仍逐次检查两节点 UID、arm64、Ready、磁盘/内存/PID 压力，并拒绝不可调度或 NoExecute 状态。
- 记录节点上报镜像数量及候选是否列出，作为观测，不作为完整缓存清单。
- 每个固定节点各创建一个短时 Job。镜像固定为 `sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638`，`imagePullPolicy=Never`、`backoffLimit=0`、180 秒截止，完成后由 TTL 控制器在 1 小时后清理。
- 固定在已核对的两个节点上检查；不使用前端 Service 的标签，不启动 Next.js，不挂生产卷、环境配置或 ServiceAccount token。
- 检查进程以 UID/GID 1000、只读文件系统、无 capabilities 运行，只核对 5 个固定文件摘要。实际 Pod 镜像 ID、所属 Job UID、节点、一次执行成功、标准输出都必须一致。
- 两边检查通过后，重新读取 Deployment/Service 基线，才允许原有单次 frontend image CAS。切换后保留双副本、HTTP 和静态资源检查。
- Job 创建和 Deployment 切换均有事前落盘的发送记录；不自动重试创建、不自动重发切换、不自动回滚。

## 验证

- Windows 离线回归 15 项通过。
- 实际封装脚本在 Linux Python 3.7 执行同组回归：14 项通过，1 项本机 Node HTTP 检查跳过；该项已在 Windows Node 环境通过。
- Linux Python 3.7 使用真实私有文件描述符，核对创建清单封存、发送日志，以及超时结果不重发，两项通过；无 Kubernetes 请求。
- 实际 arm64 候选镜像在本地网络关闭、只读、UID 1000、128 MiB/0.5 CPU 限制下执行同一 `node -e` 命令，5 个文件均通过。初次本地测试误用了文件入口导致 argv 下标不同，已改为与 Job 完全一致的 `node -e` 调用后通过，未改动候选镜像。

## 交付

脚本：`C:/Users/15285/Downloads/activate-portal-frontend-nonconfidential-notice-20261023-r3.py`。

81,410 字节，SHA256 `9cf91a6ca418c0380e8a54eda4159bed07f2ade570d677285b286e209c545e2d`。

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-frontend-nonconfidential-notice-20261023-r3.py --resume-frontend-nonconfidential-notice-once
```

## 现场切换结果

用户于 2026-10-23 贴回成功回执：`FRONTEND_NOTICE_RUNTIME_ACCEPTED_BROWSER_PENDING`，exitcode 0，耗时 39.72 秒。已存档于 `nonconfidential-notice-frontend-activation-field-20261023-r3.json`。

- 两个禁止拉取镜像的检查 Job 均执行成功，实际候选镜像及 5 个文件摘要通过。
- Frontend generation 226 → 227；两节点各一个新副本，静态文件及 HTTP 307 检查通过。
- 单次 Deployment patch 得到读回确认；Gateway 385、Runner 55、Nginx 116、Provisioner 85 的部署 spec 前后相同，Service 保持不变。
- datax-02 NodeStatus 上报 50 个镜像，候选仍未列出，但其本地候选镜像执行成功；因此已证实原检查在本次现场产生误判。0015 上报 39 个镜像且包含候选。
- 未调用模型，未执行数据库迁移。

服务器报告：`/var/lib/portal-frontend-nonconfidential-notice-activation-97f048ab80cf4666922a60e9e1092fce/REPORT.json`；32,454 字节，SHA256 `d0c3027b09a09002cb4aedeb2db63d6d49e19d5dfde856c8e8f3c1f223999f99`。

服务端上线及运行检查已完成；实际用户桌面/手机浏览器显示验收仍待确认。
