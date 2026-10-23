# 单独开启 OA v2 多文件，回调保持关闭

状态：工具已完成离线检查，尚无本次生产执行回执。2026-10-23 用户明确要求开启 v2 多文件；此前已授权 Gateway 更新可中断活动任务。本工具不要求 OA 回调 TLS 通过，因为本轮保持回调关闭，且不会访问回调地址。

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
