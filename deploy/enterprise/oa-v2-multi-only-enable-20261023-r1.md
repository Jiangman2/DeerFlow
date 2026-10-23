# 单独开启 OA v2 多文件，回调保持关闭

状态：2026-10-23 已收到更新提交成功、等待滚动超时的回执，尚未完成全部副本运行验收。用户随后提供的只读状态为 Gateway 386、已更新 51/70、就绪和可用均为 70、总副本 71、终止中 1；没有暂停，Progressing 为 True。不要将此状态登记为全量启用成功。用户曾考虑暂停，随后明确决定继续更新。本工具保持回调关闭，不访问回调地址。

将 `enable-portal-oa-v2-multi-only-20261023-r1.py` 复制到 datax-01 `/home/cnooc/` 后执行：

```bash
sudo python3 -I -B -u /home/cnooc/enable-portal-oa-v2-multi-only-20261023-r1.py --enable-oa-v2-multi-only-once --allow-active-task-interruption
```

包为 32,930 字节，SHA-256 `ae154cfcac94676421c37b188ce15ee5097ae436c18dad2d568835151a114b74`。由 `package-oa-v2-multi-only.py` 将 `enable-oa-v2-multi-only.py` 和共用预检源码打包为单文件，不需传输镜像或再次输入接口密钥。

基线来自红字前端成功回执与操作员最新镜像查询：Gateway 385、Frontend 227、PPT Runner 55、Nginx 116、Provisioner 85。执行时重新验证部署 UID、generation、完整 spec 摘要与就绪状态，读取两个 Gateway 节点的 OA 非敏感配置及四份已发布源码摘要。Gateway 和前端的成功报告还须通过服务器原文件摘要核对。

仅在 OA 原有 API 已开启、回调关闭且两个样本配置一致时继续。其他 OA 配置以当前进程实测值为基线并在更新后比对，避免覆盖同事后来调整的 CA 或额度。仅给 Gateway 添加或替换两个环境变量：

- `OA_POLICY_FLOW_MULTI_RESULT_ENABLED=true`
- `OA_POLICY_FLOW_CALLBACK_ENABLED=false`（显式保持关闭，避免重新启动时继承一个意外开启的值）

不修改镜像、Secret、ConfigMap 或数据库结构。使用 UID/resourceVersion 条件补丁且最多提交一次；Gateway 会滚动替换 70 个副本，活动任务可能中断。全部副本就绪后核验源码与配置，并在两个不同节点核验已有鉴权查询路由。Frontend、PPT Runner、Nginx 和 Provisioner 的部署保持原样。最多四次固定回环 GET，不发模型请求、业务任务或 OA 回调。

如果当前 v2 已开启且回调关闭，先完成全部副本核验，再返回 `OA_V2_MULTI_ALREADY_ENABLED_CALLBACK_DISABLED`，不重复更新。正常更新成功状态为 `OA_V2_MULTI_ENABLED_CALLBACK_DISABLED_BUSINESS_PENDING`。两者均不代表真实制度生成、逐文件下载与 OA 渲染已经业务验收。

一旦开始更新，记录保存在 `/var/lib/portal-oa-v2-multi-enable-20261023-r1/REPORT.json`。失败或结果不明确时保留完整 JSON 和报告，不删除目录、不直接重跑。没有自动重试或自动回滚；部署发生其他修改时拒绝更新。

OA 方提交多文件任务时须在原 multipart 请求中加入 `result_protocol=v2`，沿用现有凭据与 `Idempotency-Key`；查询响应读取 `results` 并逐文件下载。不传版本仍为 v1，不会自动把旧请求改成多文件，历史任务不自动重新生成。协议详情继续使用 OA V1.2 文档；回调证书问题单独处理。

离线验证：20 项检查在本机和 Linux Python 3.12 均通过，覆盖字段级条件补丁、配置保留、重复键拒绝、并发变更拒绝、单次更新、超时不重试、70 副本检查、已有开关免重复更新和禁用回调。服务器 Python 3.7 包及内嵌检查代码独立编译验证，不连接生产服务。

## 滚动观察超时后的只读续验

原工具只发出一次更新，并已通过读回确认。其 1800 秒观察期限到达不取消 Kubernetes 滚动，也不会自动重试或回滚。现场摘要见 `oa-v2-multi-timeout-observation-20261023.json`；服务器原始完整报告仍保留在原目录，8,717 字节，SHA-256 `d656711cd54e0f4aaa1f31703d7b05fe63494246518ebf363e508479986e8270`。

部署完成后，使用 `verify-portal-oa-v2-multi-after-timeout-20261023-r1.py --verify-current-readonly` 续验。单文件由 `package-oa-v2-multi-after-timeout.py` 构建，绑定原超时报告与 Gateway 386 的完整 spec 摘要。包仅包含只读查询及运行验证函数，不包含原启用入口或更新函数；不保存或覆盖服务器文件，不更改工作负载，不调用模型、业务提交或 OA 回调。

未全部就绪时仅返回当前计数和归属于该 Deployment 的 Pod 状态，状态为 `OA_V2_ROLLOUT_NOT_READY`，不会启动运行验收。全部就绪后核对全部 70 个副本的源码和实际配置，并在两个节点对固定不存在的任务发起鉴权回环 GET。确认前后部署与 Pod 身份一致后，才返回 `OA_V2_MULTI_RUNTIME_ACCEPTED_CALLBACK_DISABLED`。该结果仍不代表 OA 端真实制度生成、下载已通过业务验收。

暂停/恢复或同事的其他部署更新会改变绑定，工具据此停止并要求重新核对；不自动适配新基线。只读核验允许在收到新的滚动状态后重新执行，不重复触发滚动。新增离线测试覆盖未完成滚动不执行运行检查、旧副本仍终止时拒绝完成、报告与部署变更拒绝、70 副本核验、查询命令白名单及诊断字段脱敏。
