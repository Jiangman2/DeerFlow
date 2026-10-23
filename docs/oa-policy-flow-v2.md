# OA 制度文生图 v2 多文件交付

状态（2026-10-23）：操作员回执确认联合 Gateway generation 384 的 70 个副本及问数进度、Token 状态前端 generation 226 的两个副本已完成运行核验。PPT Runner 保持兼容版本 generation 55，数据库已扩展至 `0021_oa_result_sets`。OA 多文件代码已部署，但最近回执中的 `OA_POLICY_FLOW_MULTI_RESULT_ENABLED` 仍为 false；真实 OA 联调尚未完成。当前进入配置核对、受控启用和业务联调阶段，无需重复执行已完成的镜像预载或加列迁移。证据是操作员提交的回执，不代表持续读取了集群实时状态。

最新启用范围：用户已明确要求同时打开 OA API、v2 多文件和主动回调。`enable-oa-v2-integration.py` 及其打包器已准备，绑定已验收回执和上述基线；仅更新 Gateway 的三个开关环境变量，保留现有固定回调地址、HMAC 密钥和其他配置。运行命令与核验边界见 `deploy/enterprise/oa-v2-integration-enable-20261023-r1.md`。尚未收到启用回执，不能宣称三个开关均已生效。

随后收到启用失败回执：回调 HTTPS HEAD 出现 `ConnectError`，`patch_request_count=0`，因此本次没有修改开关或滚动服务。已准备两个节点的只读 DNS/TCP/TLS 与既有 CA 对照诊断；暂不能把失败归因于证书、代理或 OA 应用。继续以最新成功启用通知作为 v2 和回调开放依据。

后续 r1 诊断已把连接错误缩小至 TLS：两节点 DNS/TCP 正常，直连 HTTPS 均报自签名证书验证失败（OpenSSL 18）。既有 CA 对照受诊断脚本的 ASCII 解码缺陷影响而未执行；已准备使用 OpenSSL 原生文件加载的 r2，不能将 r1 的 CA `UNUSABLE` 标签当作证书文件损坏结论。最新成功启用通知仍未取得。

OA 接口文档：[V1.2 Word 规范](集团OA制度文生图数据服务接口规范说明文档_V1.2.docx)。它直接以原 V1.1 为模板，保留封面、页面设置、样式与页脚（仅更新版本）；完整说明 v1 兼容、v2 提交/查询/逐文件下载、回调文件映射、HMAC 和重试幂等。文档共 9 页，已逐页渲染检查；其中验签示例通过 20 项合成检查。文档及配置启用验证均不等于 OA 业务验收。

## 现场问题与证据

2026-10-21 操作员在 Gateway generation 382、镜像 `eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5` 上执行只读查询：

- SAP 案例 `b86f9ae1-aadb-4596-a51e-fe96193bb365`：唯一关联 run `0966c231-65a8-4ab1-bb22-7075844842d7` 成功，OA job 因 `invalid_artifact_count` 失败。多文件超出 v1 单文件契约。
- 董事会案例 `03729777-db68-4f44-a4b0-49505b7fa0a5`：唯一关联 run `6607f122-6c12-4b45-8df6-352059fb9116` 为 error，`stop_reason=recursion_capped`，预算 300。事件 36/37 为 `present_files` 调用/成功回执，事件 38 为最后助手响应，事件 39 为 run.error。最后持久检查点含 40 条消息。期间有重复参数的写入尝试，但也有持续写入、检查及最终交付，不能据此断言全程空转或将历史 error 自动改成成功。
- 两个 job 均 `callback_status=disabled`、`callback_attempts=0`；不是平台发送回调后遭 OA 拒绝。
- 该次检查时数据库迁移版本为 `0020_ppt_confirmation`。最新检查点含绑定 run 元数据，但董事会检查点时间晚于失败时间；不可把“最新检查点”当作历史失败瞬间的完整快照。

候选修复为 OA 新任务单独提供 `OA_POLICY_FLOW_RECURSION_LIMIT`（默认 1000，允许 100–2000），继续受 `AppConfig.max_recursion_limit` 上限约束，不更改普通对话或定时任务默认预算。原运行截止时间、循环防护、结果验收均保留。现场已核对全局上限 2000、scheduler.recursion_limit=300；因此 OA 新预算无需修改全局或 scheduler 配置。现场 services.py 摘要为 `103f8ccdaae4ac0335dfa049b3a767c89808cd83e09ad821411a95c3fe24ef6d`。历史失败任务不自动重放、改状态或补发通知。

## 提交和查询

现有提交 URL、机器鉴权、`request_id`、`key_fields`、文件上传与幂等头不变。增加可选 multipart 文本字段：

```text
result_protocol=v2
```

不传或传 `v1` 保持旧单文件协议。未知版本返回 422；服务端未启用 `OA_POLICY_FLOW_MULTI_RESULT_ENABLED=true` 时 v2 返回 503。同一 Idempotency-Key 改变协议版本属于不同请求，返回 409。

v2 状态查询增加 `result_protocol: "v2"` 和 `results`。旧单值 `result_filename` / `result_sha256` 对 v2 为 null，不隐式选取第一份文件。清单结构：

```json
{
  "result_protocol": "v2",
  "results": [
    {"id":"result_001","part_name":"result_001","filename":"流程一.json","bytes":123,"sha256":"64位小写十六进制摘要"},
    {"id":"result_002","part_name":"result_002","filename":"流程二.json","bytes":456,"sha256":"64位小写十六进制摘要"}
  ]
}
```

成功后逐份调用 `GET /api/v1/integrations/oa/policy-flow/jobs/{job_id}/results/{result_id}`，使用同一机器鉴权。文件 ID 来自已验收清单，调用方不能选择路径。下载返回 attachment、SHA-256 ETag 和 no-store；快照缺失或摘要不符返回 410。v2 调用旧 `/result` 返回 409，防止无声丢失其余文件。

## 回调与签名

回调仍由服务器固定地址和凭据控制。v1 manifest、`result_file` 和签名算法不变。v2 成功回调：

- multipart `manifest` 为 UTF-8 JSON 文本，含 job_id、request_id、status、key_fields、completed_at、error_code/error_detail，以及 `result_protocol` 和 `results`。
- v2 manifest 不再包含单值 result_filename/result_sha256。
- 每个 `results[i].part_name` 对应一个独立 multipart 文件部分，文件名、字节数及 SHA-256 必须逐份匹配。失败通知的 results 为空，不附带文件。
- OA 必须全部校验且幂等持久化后再返回 2xx。以 `Idempotency-Key: oa-policy-flow-callback:{job_id}` 去重；部分接收后重试不能重复创建流程。
- `X-OA-Callback-Timestamp` 为 Unix 秒。v2 签名为 `sha256=` 加 HMAC-SHA256 小写十六进制，消息字节为 `UTF8("v2." + timestamp + "." + manifest原始文本)`。manifest 已绑定全部有序文件摘要、大小和名字。必须对收到的原始文本验签，不能先解析重排 JSON。OA 应校验时间窗口并用恒定时间比较签名。
- 网络错误及 408/409/425/429/5xx 有界重试；任务生成成功状态不因传输失败降为失败。可通过逐文件下载补偿。

## 验收与存储边界

默认最多 10 份，单份沿用 `OA_POLICY_FLOW_MAX_RESULT_BYTES` 默认 10 MiB，总量由 `OA_POLICY_FLOW_MAX_TOTAL_RESULT_BYTES` 默认 50 MiB 限制。数量可配置至最多 32，总量至最多 100 MiB。对整批执行 UTF-8 严格 JSON 校验，拒绝 NaN、重复 JSON key、重复 artifact、重名（Unicode NFC/大小写归一）、跨任务路径、非法文件名和超限。任一失败则整批失败，不合并独立文件、不部分成功。JSON 协议校验不等于业务流程正确性，OA 联调仍需真实 JointJS 样本验收。

v2 必须绑定原 OA 唯一成功 run 及其检查点元数据。Agent 提交全部文件后，在 Sandbox 挂载之外的任务目录 `oa-delivery` 创建内容寻址快照，再保存数据库清单。后续 Agent 修改原输出不影响已经验收的交付内容；回调和下载均重验快照摘要。快照沿用所属用户/任务存储和生命周期，不能直接暴露文件系统路径。

## 发布与回滚

新增迁移 `0021_oa_result_sets`，只增加 `result_protocol`（存量默认 v1）和可空 `result_files`。部署前核对现场迁移链和源码摘要，显式执行该加列迁移，不借本次热更运行未核对的其他迁移。

2026-10-21 现场只读核对确认 Gateway generation 382 与 PPT Runner generation 54 的数据库地址指纹相同，两者都在启动时初始化数据库，且镜像迁移 head 均为 `0020_ppt_confirmation`。原镜像不能识别 `0021`，所以“保留新增列、直接切回原镜像”并不是完整回滚方案。

发布必须分阶段：先为所有共用数据库的旧消费者准备并验收窄范围启动兼容补丁，保持本地迁移 head 为 0020，不提前加列；补丁仅在数据库版本恰为 0021 且两个新增列的类型、默认值和空值约束准确时接受该已知后继版本，既不改写版本号也不运行降级。未知版本和不符的列结构仍拒绝。Gateway 的兼容旧业务镜像同时作为回滚目标，PPT Runner 保留原任务逻辑。兼容组件全部就绪后才执行 0021，再更新联合 Gateway，并保持 v2 入口关闭。

全部 Gateway 更新完成、候选检查通过且双方约定联调方式后，受控启用 `OA_POLICY_FLOW_MULTI_RESULT_ENABLED=true`，再让 OA 用新测试请求发送版本字段。配置在服务启动时加载，变更后需要完成 Gateway 配置滚动更新；无需再次加列或重建同一功能镜像。若先验证查询及下载，可保留回调关闭；若验证推送，须先确认 OA 接收地址与鉴权配置。不得在新旧 worker 混跑时提交 v2，因为旧 worker 不认识多文件清单。真实样本、逐文件校验及需要的回调验收通过后再扩大业务使用。回滚到兼容迁移版本的 v1 业务镜像前必须停止新 v2 提交并排空 v2 处理/回调任务；保留新增列及快照，禁止降级删除清单。原 0020 镜像不能作为该阶段的直接回滚目标。回滚必须保留已上线问数、GLM 和其他同事修改。兼容阶段和联合功能镜像的合成运行检查已验收，真实 OA 业务验收仍待完成。

仍需现场验证：两份/三份真实文件、OA 验签及部分接收重试、原单文件调用、OA 独立步数预算，以及真实 OA 接口业务验收。

2026-10-23 收到操作员成功切换回执：联合 Gateway 已升至 generation 384，70 个副本运行验收通过；问数进度及 Token 状态前端已升至 generation 226，两个副本的源码和静态资源验收通过。PPT Runner 保持兼容版本 generation 55，数据库保持 `0021_oa_result_sets`。这些回执不等于真实 OA 业务验收，`OA_POLICY_FLOW_MULTI_RESULT_ENABLED` 仍为 false；不得宣称 OA 已能在生产调用 v2 多文件协议。回执分别归档于 `deploy/enterprise/oa-feature-activation-field-20261023-r1.json` 和 `deploy/enterprise/data-qa-token-frontend-activation-field-20261023-r1.json`。

2026-10-22 已验收的兼容版本：Gateway `sha256:361592fec9d1915fb0237c27f957f4b925899a017b53081d71d67425d75b10b5`，PPT Runner `sha256:0b0cdbbbf95794ab0ce6f96e09f6a449152d05761c23b47dcda3d5c9cf2a3140`。每个镜像仅增加 bootstrap.py 与 oa_schema_compatibility.py；分别使用现场核对过的旧版启动源码，原迁移目录与业务代码不变。预载包分别限定原有五台 Gateway 节点与十台 Runner 预载节点，保留原镜像引用。r4 根据操作员明确允许中断任务的指示继续切换，并保留前后各 3 条 active run 的数量；该数量不能证明这 3 条任务是否完成、恢复或失败。
