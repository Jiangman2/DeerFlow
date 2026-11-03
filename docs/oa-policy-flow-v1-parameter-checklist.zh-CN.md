# 集团 OA 制度文生图接口联调参数与责任清单

- 文档版本：V1.2（轮询模式）
- 接口提供方：智能体平台
- 接口使用方：集团 OA
- 当前状态：OA 已确认采用轮询模式；回调关闭，待轮询模式 Gateway 热更后启用联调

## 1 联调结论

我方提供任务提交、状态查询和结果下载三个接口。OA 调用我方时使用我方签发的接入密钥，并通过状态查询和结果下载完成全流程；本轮联调不启用我方向 OA 的回调。

接口当前保持关闭。轮询模式 Gateway 热更完成、接入密钥交付且网络白名单确认后，我方启用测试联调。

## 2 接口方向

| 序号 | 调用方向 | 方法与地址 | 用途 |
| --- | --- | --- | --- |
| 1 | OA 到智能体平台 | `POST https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs` | 上传制度文件并创建异步任务 |
| 2 | OA 到智能体平台 | `GET https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs/{job_id}` | 轮询任务状态和结果摘要 |
| 3 | OA 到智能体平台 | `GET https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs/{job_id}/result` | 下载成功任务的 JointJS JSON 结果 |

本轮不调用 OA 测试或生产回调地址。服务端固定设置
`OA_POLICY_FLOW_CALLBACK_ENABLED=false`；任务终态返回
`callback_status=disabled`。

## 3 我方已确定的参数

| 项目 | V1.1 固定值或规则 |
| --- | --- |
| 接口状态 | OA 参数未确认前保持关闭 |
| 目标业务能力 | 制度文生图智能体 |
| OA 调用我方鉴权 | `Authorization: Bearer <我方签发的接入密钥>` |
| 结果获取模式 | OA 主动轮询状态并下载结果；服务端回调关闭 |
| 允许文件 | `.doc`、`.docx`、`.pdf` |
| 单文件上限 | 10 MiB，文件不得为空 |
| `key_fields` 上限 | 64 KiB，必须是严格 JSON 对象，不允许重复键和非有限数值 |
| 幂等键 | 必填，8 至 200 个可见 ASCII 字符；格式为 `policy:<request_id>` |
| 后台并发 | 最多同时处理 2 个 OA 制度任务 |
| 智能体运行上限 | 1800 秒 |
| OA 查询建议间隔 | 10 至 30 秒 |
| OA 整体等待窗口 | 40 分钟 |
| 结果完整性 | 状态查询返回的 `result_sha256` 必须与下载文件 SHA-256 一致 |
| HTTPS | OA 调用三个接口时必须使用生产统一 HTTPS 入口 |

## 4 OA 提交字段契约

### 4.1 请求头

```http
Authorization: Bearer <我方通过安全渠道提供的接入密钥>
Idempotency-Key: policy:<request_id>
Content-Type: multipart/form-data
```

同一逻辑请求重试时必须复用相同 `Idempotency-Key`。相同幂等键及相同文件和字段返回原任务；相同幂等键对应不同内容时返回 HTTP 409。

### 4.2 Multipart 字段

| 字段 | 类型 | 必填 | V1.1 约束 |
| --- | --- | --- | --- |
| `document` | file | 是 | 一个 DOC、DOCX 或 PDF 文件，最大 10 MiB |
| `request_id` | string | 是 | OA 本次业务请求唯一编号，1 至 128 字符；建议使用 UUID 或仅含 ASCII 的流程实例号 |
| `key_fields` | string | 是 | 下表 JSON 对象的 UTF-8 字符串，最大 64 KiB |

### 4.3 key_fields V1.1

| 字段 | 类型 | 必填 | 最大长度 | 说明 |
| --- | --- | --- | --- | --- |
| `documentCode` | string | 是 | 128 | 制度编码，例如 `CG-02-03` |
| `documentName` | string | 是 | 256 | 制度完整名称 |
| `version` | string | 是 | 64 | 制度版本，例如 `V1.0` |
| `organizationCode` | string | 否 | 64 | 制度所属组织编码 |

V1.1 不增加未登记字段。确需扩展时，双方先确认兼容性，再更新文档版本。

示例：

```json
{
  "documentCode": "CG-02-03",
  "documentName": "中国海洋石油集团有限公司内控制度体系文件管理办法",
  "version": "V1.0",
  "organizationCode": "10001"
}
```

## 5 任务状态与补偿

任务状态包括：`queued`、`launching`、`running`、`succeeded`、`failed`。

轮询模式下，处理中的任务回调状态为 `not_ready`，终态任务为
`disabled`。OA 只根据任务 `status` 判断处理结果，不等待回调状态。

OA 必须保存创建接口返回的 `job_id`，后续查询和下载均以 `job_id` 为准。

## 6 回调模式状态

本轮联调和生产方案均采用 OA 主动轮询，以下原回调协议不启用。OA
无需开发回调接收接口、HMAC 验签或回调幂等处理；以后如双方重新决定
启用回调，应另行评审并更新接口文档版本。

<!--

回调请求使用 `multipart/form-data`：

| 字段 | 成功任务 | 失败任务 | 说明 |
| --- | --- | --- | --- |
| `manifest` | 有 | 有 | UTF-8 JSON，包含任务状态、原业务字段、摘要或错误信息 |
| `result_file` | 有 | 无 | UTF-8 JointJS JSON 文件，直接二进制传输 |

回调请求头：

```http
Idempotency-Key: oa-policy-flow-callback:<job_id>
X-OA-Callback-Timestamp: <Unix 秒级时间戳>
X-OA-Callback-Signature: sha256=<HMAC 十六进制签名>
```

签名原文：

```text
<timestamp>.<manifest 字段原始 UTF-8 JSON 字符串>.<结果文件 SHA-256；失败时为空字符串>
```

OA 应校验时间戳新鲜度，使用常量时间比较验签，以 `job_id` 幂等处理重复回调，成功任务还必须计算结果文件 SHA-256。只有在状态与文件均持久化成功后才能返回 2xx。
-->

## 7 OA 必须提供或确认的参数

| 序号 | OA 待提供或确认项 | 要求 |
| --- | --- | --- |
| 1 | OA 出口网络 | 确认能够访问 `cnoocclaw.cnooc.com.cn:443`，并说明白名单要求 |
| 2 | 接入密钥保管 | 确认通过受控渠道接收并安全保存 Bearer 接入密钥 |
| 3 | 轮询策略 | 以 10 至 30 秒间隔查询，最长等待 40 分钟，终态立即停止轮询 |
| 4 | 幂等处理 | 同一逻辑任务重试时复用相同 `Idempotency-Key` |
| 5 | SHA-256 | 下载成功结果后计算 SHA-256 并与状态接口比较 |
| 6 | JointJS 结果 | 使用已提供样本确认 OA 页面能够加载；输出业务结构作为双方验收基线 |
| 7 | 联调安排 | 提供联系人、联调窗口、OA 日志查询方式及故障反馈渠道 |

## 8 安全材料交付

我方向 OA 提供一项机密材料：

1. OA 调用我方三个接口使用的接入密钥。

机密材料不得写入接口文档、普通邮件正文、即时通信消息或代码仓库。双方应通过公司批准的密码保险库、密钥管理系统或其他受控渠道交付，并记录接收人、环境和生效时间。

## 9 OA 回复表

```text
OA 是否可访问 cnoocclaw.cnooc.com.cn:443：是 / 否
OA 出口 IP 或白名单要求：
是否接受 10 至 30 秒轮询间隔：是 / 否
是否接受最长 40 分钟等待窗口：是 / 否
是否按 Idempotency-Key 幂等提交：是 / 否
是否校验下载结果 SHA-256：是 / 否
JointJS 样本加载结果：通过 / 不通过 / 待验证
OA 联调联系人：
OA 可联调时间：
```

## 10 联调启用条件

同时满足以下条件后才启用接口：

1. OA 轮询参数回复确认完成；
2. 轮询模式 Gateway 热更已部署；
3. OA 到我方统一入口的 DNS、TCP、TLS 和白名单检查通过；
4. 我方接入密钥已通过安全渠道交付；
5. 服务器目标用户和制度文生图智能体检查通过；
6. 数据库无历史待恢复 OA 任务；
7. 当前普通智能体运行任务已结束，避免重启影响用户；
8. 双方处于约定联调窗口。

## 11 联调验收用例

至少完成以下测试：正常 DOCX、DOC 和 PDF；同幂等键同内容重试；同幂等键不同内容返回 409；错误密钥返回 401；非法、空文件和超限文件被拒绝；轮询直到成功或失败；成功后下载结果；状态与下载文件 SHA-256 一致；成功 JSON 能被 OA JointJS 页面加载；失败任务返回明确错误且不能下载结果。
