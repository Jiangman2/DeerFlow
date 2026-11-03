# 集团 OA 制度文生图接口联调说明

- 文档版本：V1.0
- 文档状态：联调稿（生产启用前需双方确认回调地址、鉴权和关键字段）
- 更新日期：2026-09-02

## 1. 接口目标

集团 OA 上传制度文件及相关业务字段后，通用智能体异步调用制度文生图智能体，生成可供 JointJS 使用的 JSON 流程文件。任务成功或失败后，通用智能体主动回调 OA；OA 也可查询任务状态并补偿下载成功结果。

生产环境基础地址：

```text
https://cnoocclaw.cnooc.com.cn
```

文件在两个方向均采用 `multipart/form-data` 的普通文件字段传输，不使用 Base64。

## 2. 整体流程

1. OA 调用任务提交接口，上传一个制度文件、OA 业务请求号和关键字段。
2. 通用智能体完成鉴权、文件安全检查和任务持久化后，立即返回 `202 Accepted` 及 `job_id`。
3. 通用智能体在后台解析文件并启动智能体，不保持原提交请求连接。
4. 智能体完成后，通用智能体校验并保存唯一的 JSON 结果文件。
5. 通用智能体调用 OA 提供的固定回调接口，回传任务结果和原关键字段。
6. 如果回调暂时失败，通用智能体按规则重试；OA 可使用 `job_id` 查询状态并下载成功结果。

## 3. 鉴权与幂等

### 3.1 OA 调用通用智能体

通用智能体向 OA 提供一个专用接口访问密钥。OA 请求需携带：

```http
Authorization: Bearer <通用智能体提供的接口访问密钥>
Idempotency-Key: <本次业务提交的稳定幂等键>
```

也可按双方约定使用 `X-OA-Integration-Key` 请求头代替 Bearer Header。

`Idempotency-Key` 要求：

- 8–200 个可见 ASCII 字符；
- 同一笔逻辑业务重试时必须保持不变；
- 相同幂等键及相同请求内容返回原任务，不会重复启动智能体；
- 相同幂等键对应不同文件或字段时返回 `409 Conflict`。

建议格式：

```text
policy:<OA制度ID>:<版本号>
```

### 3.2 通用智能体回调 OA

OA 应提供专用回调鉴权方式，支持以下一种或同时支持两种：

- Bearer Token：由 OA 提供专用 Token；
- HMAC-SHA256：双方通过安全渠道共享签名密钥。

回调请求可能携带：

```http
Authorization: Bearer <OA提供的回调Token>
Idempotency-Key: oa-policy-flow-callback:<job_id>
X-OA-Callback-Timestamp: <Unix秒级时间戳>
X-OA-Callback-Signature: sha256=<HMAC十六进制签名>
```

HMAC 原文为：

```text
<timestamp>.<manifest字段原始UTF-8 JSON字符串>.<结果文件SHA-256；失败时为空字符串>
```

manifest 使用回调 multipart 中 `manifest` 字段收到的原始 UTF-8 字符串，不要解析后重新序列化。OA 应校验时间戳新鲜度、使用常量时间比较验签，并以 `job_id` 幂等处理重复回调。

## 4. 提交任务接口

### 4.1 请求

```http
POST /api/v1/integrations/oa/policy-flow/jobs
Content-Type: multipart/form-data
Authorization: Bearer <接口访问密钥>
Idempotency-Key: policy:ZD-2026-001:V3
```

Multipart 字段：

| 字段 | 类型 | 必填 | 约束及说明 |
| --- | --- | --- | --- |
| `document` | file | 是 | 一个 `.doc`、`.docx` 或 `.pdf` 制度文件；不能为空；最大 10 MiB |
| `request_id` | string | 是 | OA 业务请求号，1–128 个字符 |
| `key_fields` | string | 是 | 严格 JSON 对象字符串，最大 64 KiB；成功或失败回调时按同一 JSON 对象返回，字段顺序不保证 |

`key_fields` 的字段名由 OA 提供并由双方最终确认。联调示例：

```json
{
  "oaDocumentId": "ZD-2026-001",
  "title": "采购管理制度",
  "version": "V3.0",
  "organizationCode": "10001"
}
```

示例：

```bash
curl --fail-with-body \
  --connect-timeout 10 \
  --max-time 60 \
  -X POST \
  'https://cnoocclaw.cnooc.com.cn/api/v1/integrations/oa/policy-flow/jobs' \
  -H 'Authorization: Bearer REPLACE_WITH_AGENT_KEY' \
  -H 'Idempotency-Key: policy:ZD-2026-001:V3' \
  -F 'request_id=ZD-2026-001' \
  -F 'key_fields={"oaDocumentId":"ZD-2026-001","title":"采购管理制度","version":"V3.0","organizationCode":"10001"}' \
  -F 'document=@采购管理制度.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document'
```

### 4.2 成功受理响应

HTTP 状态码：`202 Accepted`

```json
{
  "job_id": "54a4dd5b-bc52-42f6-a5ed-559e7162fc15",
  "request_id": "ZD-2026-001",
  "status": "queued",
  "callback_status": "not_ready",
  "created_at": "2026-09-02T01:20:00+00:00",
  "updated_at": "2026-09-02T01:20:00+00:00",
  "completed_at": null,
  "delivered_at": null,
  "error_code": null,
  "error_detail": null,
  "callback_error": null,
  "result_filename": null,
  "result_sha256": null
}
```

OA 必须保存 `job_id`，后续状态查询、结果下载和回调幂等均以它为准。

## 5. 查询任务状态接口

```http
GET /api/v1/integrations/oa/policy-flow/jobs/{job_id}
Authorization: Bearer <接口访问密钥>
```

任务 `status`：

| 值 | 含义 |
| --- | --- |
| `queued` | 已受理，等待后台处理 |
| `launching` | 正在解析制度并启动智能体 |
| `running` | 智能体正在运行 |
| `succeeded` | JSON 结果已通过基础校验并保存 |
| `failed` | 任务失败，查看 `error_code` 和 `error_detail` |

回调 `callback_status`：

| 值 | 含义 |
| --- | --- |
| `not_ready` | 尚未进入回调阶段 |
| `pending` | 等待回调或等待重试 |
| `delivered` | OA 已返回 2xx，回调交付完成 |
| `exhausted` | 回调遇到不可重试错误或重试次数耗尽 |

任务成功状态不会因为回调失败而变为失败。此时 OA 可通过结果下载接口补偿获取文件。

## 6. 下载成功结果接口

```http
GET /api/v1/integrations/oa/policy-flow/jobs/{job_id}/result
Authorization: Bearer <接口访问密钥>
```

仅当 `status=succeeded` 时返回：

```http
Content-Type: application/json
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
ETag: "<result_sha256>"
```

任务未成功时返回 `409`；已接受的结果文件不再可用时返回 `410`。

## 7. 通用智能体回调 OA

### 7.1 OA 需要提供的回调接口

```http
POST <OA提供的固定HTTPS回调URL>
Content-Type: multipart/form-data
```

Multipart 字段：

| 字段 | 类型 | 成功任务 | 失败任务 | 说明 |
| --- | --- | --- | --- | --- |
| `manifest` | JSON part | 有 | 有 | 任务状态、业务字段、文件摘要或错误信息 |
| `result_file` | file | 有 | 无 | UTF-8 JointJS JSON 文件，直接二进制传输 |

成功 `manifest` 示例：

```json
{
  "job_id": "54a4dd5b-bc52-42f6-a5ed-559e7162fc15",
  "request_id": "ZD-2026-001",
  "status": "succeeded",
  "key_fields": {
    "oaDocumentId": "ZD-2026-001",
    "title": "采购管理制度",
    "version": "V3.0",
    "organizationCode": "10001"
  },
  "result_filename": "采购管理制度-流程.json",
  "result_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "error_code": null,
  "error_detail": null,
  "completed_at": "2026-09-02T01:40:00+00:00"
}
```

失败 `manifest` 示例：

```json
{
  "job_id": "54a4dd5b-bc52-42f6-a5ed-559e7162fc15",
  "request_id": "ZD-2026-001",
  "status": "failed",
  "key_fields": {
    "oaDocumentId": "ZD-2026-001"
  },
  "result_filename": null,
  "result_sha256": null,
  "error_code": "agent_run_timeout",
  "error_detail": "Agent run exceeded the 1800-second limit",
  "completed_at": "2026-09-02T01:50:00+00:00"
}
```

### 7.2 OA 回调处理要求

OA 回调服务应：

1. 验证 Bearer Token 和/或 HMAC 签名；
2. 以 `job_id` 幂等，重复回调不得重复创建业务结果；
3. 成功任务计算 `result_file` 的 SHA-256，并与 `manifest.result_sha256` 比较；
4. 将业务状态、关键字段及文件持久化成功后再返回 2xx；
5. 对已成功持久化的重复回调直接返回 2xx；
6. 返回非 2xx 时，理解通用智能体可能按规则再次回调。

通用智能体对网络错误、HTTP `408`、`409`、`425`、`429` 和 `5xx` 进行有界重试；其他 `4xx` 视为协议错误，不持续重试。

## 8. 超时建议

| 环节 | 建议/限制 |
| --- | --- |
| OA 提交任务 HTTP 超时 | 60 秒 |
| 智能体运行上限 | 1,800 秒（30 分钟） |
| 通用智能体单次回调总超时 | 30 秒 |
| OA 整体业务等待窗口 | 40 分钟 |
| OA 主动查询间隔 | 建议 10–30 秒，不要高频轮询 |

制度解析时间不占智能体的 30 分钟运行预算。超过智能体运行上限后，任务标记为 `failed`，错误码为 `agent_run_timeout`，并正常回调 OA。

## 9. 常见 HTTP 状态码

| HTTP 状态码 | 含义 |
| --- | --- |
| `202` | 任务已受理 |
| `401` | 通用智能体接口访问密钥无效 |
| `404` | `job_id` 不存在 |
| `409` | 幂等键冲突，或结果尚不可下载 |
| `410` | 已接受的结果文件不再可用 |
| `413` | 文件或 `key_fields` 超过限制 |
| `415` | 文件格式不是 doc/docx/pdf |
| `422` | 请求字段、幂等键或 JSON 格式无效 |
| `503` | 接口尚未启用或后台服务不可用 |

## 10. OA 方需要完成的工作

联调前，OA 方需要：

- 确定并提供测试、生产两个固定 HTTPS 回调 URL；
- 实现接收 `multipart/form-data` 的回调接口；
- 确定回调鉴权方式，并通过安全渠道提供 Bearer Token，或与通用智能体交换 HMAC 密钥；
- 给出 `key_fields` 的正式字段清单、字段类型、是否必填及示例；
- 在提交成功后保存 `job_id`，实现异步状态展示；
- 按 `job_id` 实现回调幂等和 SHA-256 校验；
- 实现回调异常时的状态查询和结果下载补偿；
- 确认 OA 入口是否需要配置通用智能体出口 IP 白名单；
- 使用 OA 的 JointJS 渲染器验证实际 JSON 结果是否符合固化格式。

## 11. 通用智能体方需要完成的工作

联调前，通用智能体方需要：

- 发布包含本接口的新 Gateway 镜像和数据库迁移；
- 在服务端固定目标智能体和运行用户，不允许请求方选择；
- 生成 OA 调用通用智能体使用的接口访问密钥，并通过安全渠道交付；
- 配置 OA 提供的回调 URL、回调鉴权材料和必要的 CA 证书；
- 配置并验证双方网络白名单；
- 提供测试环境并协助完成成功、失败、超时、幂等和回调重试测试；
- 联调通过后轮换测试凭证，再启用生产配置。

## 12. 联调验收用例

至少验证以下场景：

1. 小型 DOCX 正常生成、回调成功、OA JointJS 正常渲染；
2. DOC 和 PDF 各完成一次正常生成；
3. 相同幂等键及相同内容重复提交，不产生第二个智能体任务；
4. 相同幂等键提交不同内容，返回 `409`；
5. 非法扩展名、空文件、超过 10 MiB 文件被拒绝；
6. 错误接口密钥返回 `401`；
7. OA 回调暂时返回 `5xx`，通用智能体重试后成功；
8. 回调不可用时，OA 能查询 `succeeded` 并通过下载接口补偿获取文件；
9. OA 对重复回调保持幂等；
10. OA 计算的文件 SHA-256 与回调及查询结果一致；
11. 失败任务能收到不含结果文件的失败回调；
12. 双方确认 30 分钟任务上限和 40 分钟业务等待窗口。

## 13. 联调前待 OA 确认项

请 OA 方回复以下信息，真实密钥不得通过普通聊天或邮件正文传输：

```text
1. 测试回调 URL：
2. 生产回调 URL：
3. 回调鉴权方式：Bearer / HMAC / 两者同时
4. 鉴权材料的安全交付方式：
5. key_fields 正式字段定义及示例：
6. 是否要求配置来源 IP 白名单：
7. 是否支持 multipart/form-data 文件接收：
8. OA 联调联系人及可联调时间：
```
