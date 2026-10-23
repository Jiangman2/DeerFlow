# OA 接口、v2 多文件与回调启用

2026-10-23：用户明确要求三个开关全部打开，并允许 Gateway 滚动替换中断活动任务。本工具已在生产执行预检，但在提交补丁前因回调 HTTPS 证书验证失败退出。r2 诊断确认 API 已启用，v2 和回调仍关闭；以下为原计划命令，证书信任问题解决并复核基线前不要重跑。

将 `enable-portal-oa-v2-integration-20261023-r1.py` 复制到 datax-01 `/home/cnooc/` 后执行：

```bash
sudo python3 -I -B -u /home/cnooc/enable-portal-oa-v2-integration-20261023-r1.py --enable-oa-v2-integration-once --allow-active-task-interruption
```

工具 32,577 字节，SHA-256 为 `a84ea8336b5cdb91073e6fb1dad806cef26025401a65d4d601104dc609360de5`。源码为 `enable-oa-v2-integration.py`，由 `package-oa-v2-integration-enable.py` 固化基线与回执后生成。

先核对 Gateway 384 / Frontend 226 / PPT Runner 55、已有回执、源文件摘要和两个 Gateway 节点的当前 OA 配置。仅对已有固定 HTTPS 回调地址发送最多两次无凭据 HEAD，保持证书校验和直连；不跟随重定向。该检查只验证目标可达，不证明 OA 接收端支持 v2 或验签成功。

仅对 Gateway 发起一次带资源版本条件的补丁，把 `OA_POLICY_FLOW_ENABLED`、`OA_POLICY_FLOW_MULTI_RESULT_ENABLED` 和 `OA_POLICY_FLOW_CALLBACK_ENABLED` 设为 true。配置在进程启动时加载，会滚动替换 70 个副本；保留现有镜像、Secret、ConfigMap、其他环境配置、前端和 PPT Runner。本次不需再次预载镜像或迁移数据库。工具不提交业务任务、不调用模型、不主动 POST 回调；开关生效后普通工作进程可按已有任务状态发送业务回调。历史 disabled 任务不自动补发。

完成后等待 70 个副本就绪，核对全部副本的源码与开关，并在两个副本执行固定本地鉴权 GET。成功回执状态为 `OA_V2_INTEGRATION_ENABLED_BUSINESS_PENDING`。这只代表平台开关与运行核验完成，真实制度生成、全部结果下载、OA 验签/持久化/渲染仍需 OA 验收。

报告保存于 `/var/lib/portal-oa-v2-integration-enable-20261023-r1/REPORT.json`。若失败或补丁结果不明确，保留报告并排查；不要删除一次性记录或盲目重跑。无自动回滚或自动重试。

离线检查：15 项启用工具检查、16 项共用预检检查、6 项 Linux Python 3.7 隔离事务场景通过；生成工具通过 Python 3.7 导入和编译检查。最终打包字节与已检查工具一致。OA 文档样式及示例检查记录见 `oa-v12-interface-document-qualification-20261023-r1.json`。

## 现场连接失败与后续诊断

操作员随后返回 `CALLBACK_HTTPS_HEAD_NOT_ACCEPTED`，首个 Gateway 样本出现 `ConnectError`。`once_claimed=false`、`patch_request_count=0`、`patch_may_have_applied=false`，确认此尝试没有提交部署补丁，也未执行滚动更新。r1 输出中的 `ordinary_worker_callbacks_enabled_by_rollout=true` 是预期行为标志，不能用来认定回调已启用。回执归档在 `oa-v2-enable-failure-field-20261023-r1.json`。

准备 `diagnose-portal-oa-callback-transport-20261023-r1.py --diagnose-readonly`，从两个 Gateway 节点分层检查固定回调地址的 DNS、TCP 和当前 HTTPX HTTPS 连接。只有当前 TLS 证书验证失败时，才用已有、可解析的企业 CA 进行一次对照；不禁用证书或主机名校验，不使用代理，不跟随重定向。最多四次不带凭据的 HEAD，不读取响应正文或输出原始错误、路径、密钥；不提交任务、不发业务 POST 回调、不访问数据库、不写生产文件和配置。

诊断包通过 12 项离线测试、Linux Python 3.7 打包及内嵌代码编译检查，并在无外网 Docker 容器中通过本地 HTTPS 对照：默认信任链拒绝合成企业证书、显式 CA 验证通过、主机名不符仍拒绝、请求不携带凭据。这些合成结果不是现场根因结论，等待服务器诊断回执后再确定网络或 CA 修复。

2026-10-23 的 r1 诊断回执显示：两个 Gateway 节点均能解析固定回调域名，TCP 443 连接成功；实际直连 HTTPS 的异常链均为证书验证失败，OpenSSL 代码 18（SELF_SIGNED_LEAF）。当前 HTTPX 信任链不接受该证书，未收到 HTTP 响应。此尝试未使用代理，不能通过添加 NO_PROXY 来修复这项证书验证错误。

r1 的 CA 元数据检查有缺陷：对整个文件 `decode('ascii')`，遇到非 ASCII 字节即标记 UNUSABLE，因而没有执行现场既有 CA 对照。这不能证明 CA 文件损坏，也不能证明现有 CA 无法验证回调。r2 改用 OpenSSL 原生 `cafile` 加载，冻结验证上下文并比对加载前后文件内容；证书和主机名验证要求不变。不抓取远端证书并自动加入信任，不关闭校验。r2 已通过 15 项离线检查、Linux Python 3.7 打包编译，以及含 UTF-8 注释 CA 的本地真实 TLS 检查。

r2 现场回执现已返回并归档在 `oa-callback-transport-field-20261023-r2.json`。两个节点的现有 CA 均加载成功（228306 字节、143 张证书，SHA-256 `84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af`），但默认信任库和显式现有 CA 的直连 HTTPS 对照都返回 OpenSSL 18 / SELF_SIGNED_LEAF。DNS 解析至 `10.72.68.182`、TCP 443 均通过。四次 HEAD 尝试均未通过 TLS；`exitcode=0` 只表示诊断完成，不表示回调可用。配置和部署未改变。

接续依赖 OA 运维确认正式回调入口及其证书：更换为主机名匹配的受信任证书并部署所需中间链，或提供经其确认的 CA/自签名公有证书和 SHA-256 指纹。当前没有抓取远端证书、颁发者、SAN、有效期或指纹，不推断它们已经通过。收到可信材料后，先验证完整 TLS 链和主机名，再准备仅用于 OA 回调的 CA 配置及启用步骤。现有 CA 直接复用不能解决本次失败；不用再次运行相同诊断或重做预载。给 OA 的排查说明见 `oa-callback-certificate-handoff-20261023-r2.txt`。
