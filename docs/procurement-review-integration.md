# 技术委托要求审查：单接口调用说明

## 1. 调用方式与参数

对方后台上传材料后保持本次 HTTP 连接；我们定时发送心跳，完成后在同一连接返回完整 Markdown 报告，再结束连接。对方前端负责渲染报告。

- 方法：`POST`
- 地址：`https://cnoocclaw.cnooc.com.cn/api/v1/integrations/procurement-review`
- 鉴权：`Authorization: Bearer <提交密钥>`
- 请求格式：`multipart/form-data`
- 响应格式：`text/event-stream`（SSE 事件流）

调用密钥由平台方单独提供，本文不包含实际密钥。由对方后台发起请求，密钥不要放进浏览器。只传两个参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `request_id` | 是 | 本次审查唯一编号，用于关联业务记录及断线重连，例如 `SCM-2026-001-review-1` |
| `files` | 是 | 多份项目材料，重复使用同名字段上传 |

材料建议包含技术委托要求书、立项论证意见、立项审批页、预算下达通知。审批完成日期应包含在立项审批信息中。制度和四份标准模板由平台提供，无需重复上传。

`request_id` 由对方生成，用于关联本次业务审查及幂等重连；材料变更后重审应使用新编号。`job_id` 是平台返回的任务号，对方保存即可，不需要提前生成或提交。

支持 PDF、DOC、DOCX、PNG、JPG、JPEG，JSON、TXT、XLSX、XLS、CSV；最多 12 份，单份不超过 30 MiB，总计不超过 100 MiB，文件名不能重复。编号为 1–128 位 ASCII 字母、数字及 `_ . : -`，以字母或数字开头。

## 2. 调用示例

在材料所在服务器上执行。文件路径按实际情况修改；缺少的材料不要填写不存在的文件路径，可继续上传现有材料，报告会说明对应无法判断项。

```bash

curl --silent --show-error --no-buffer \
  --noproxy cnoocclaw.cnooc.com.cn \
  --connect-timeout 15 --max-time 3900 \
  'https://cnoocclaw.cnooc.com.cn/api/v1/integrations/procurement-review' \
  -H "Authorization: Bearer 改为密钥" \
  -F 'request_id=SCM-2026-001-review-1' \
  -F 'files=@技术委托要求.docx' \
  -F 'files=@立项论证意见.pdf' \
  -F 'files=@立项审批页.pdf' \
  -F 'files=@预算下达通知.pdf' \
  --output procurement-response.sse

```

响应流保存在 `procurement-response.sse`，可在另一终端执行 `tail -f procurement-response.sse` 查看。该文件包含完整 SSE 封装，不是可直接渲染的 Markdown；需要提取最终 JSON 的 `report_markdown`。

这是命令行联调示例。生产接入应由后台从密钥配置读取凭据，并检查 HTTP 状态码及 SSE 最终结果。首次业务调用使用新的 `request_id`；同一次调用重连时保持原编号和材料不变。

不要手动设置 multipart boundary。3900 秒是调用端等待上限，不改变服务端任务期限；当前服务端任务期限为 3600 秒。不要手动设置 `Content-Type`，由 multipart 客户端自动生成包含 boundary 的请求头。

## 3. 同一连接返回的内容

接收成功返回 HTTP `200` 并开始事件流。每个事件以空行结束，`data:` 后是一行 JSON。以下任务号及正文为示例。`accepted.status` 可能为 `queued`、`launching`、`running` 或重连已完成任务时的 `result_ready`；收到 accepted 不等于完成。

**任务已接收：**

```text
event: accepted
data: {"request_id":"SCM-2026-001-review-1","job_id":"任务号","status":"queued"}

```

**处理中：约每 15 秒发送心跳。**

```text
event: heartbeat
data: {"request_id":"SCM-2026-001-review-1","job_id":"任务号","status":"running"}

```

**成功结束：读取 `report_markdown`，作为 Markdown 正文保存和渲染。**

```text
event: result
data: {"request_id":"SCM-2026-001-review-1","job_id":"任务号","status":"succeeded","message":"审查已完成，结论及无法判断项见审查报告。","report_format":"markdown","report_markdown":"# 技术委托要求审查报告\n\n（示例正文）\n","report_bytes":53,"report_sha256":"fe39f5692d58df10b5f0fc0efaa0ba075e51b4f24a5e60f1416f1971a34f2d4a"}

```

**执行失败：不返回报告，展示 `message`。**

```text
event: result
data: {"request_id":"SCM-2026-001-review-1","job_id":"任务号","status":"failed","message":"审查超过任务处理期限，未交付完成报告；请联系管理员。"}

```

`succeeded` 表示报告生成及程序校验成功，不代表项目合规；审查结论见报告。HTTP `200` 或连接关闭也不代表成功，必须收到 `result` 并检查 `status`。

上面仅为简短正文示例，实际响应包含完整报告。报告上限2 MiB，超限报错，不截断。对 `report_markdown` 解码后的字符串按 UTF-8 编码（不添加 BOM、不修改换行）计算字节数和 SHA-256，应分别等于 `report_bytes` 和 `report_sha256`。不要对整个 JSON、显示后的 HTML 或 UTF-16 字符串计算。调用方应先校验再保存；单个事件缓冲上限建议至少16 MiB，以容纳 JSON 转义开销。

## 运维配置与兼容性

新任务只通过同一个 HTTP SSE 连接返回 Markdown，不需要对方开发回调接口。HTTP 200 表示连接建立，最终以 `result.status` 为准；`succeeded` 表示报告交付成功，不表示采购合规。相同 request_id 和相同材料重连读取已有任务；要重新审查须更换 request_id。

启用需配置 PROCUREMENT_REVIEW_ENABLED、OWNER_USER_ID、ASSISTANT_ID、REFERENCE_ROOT、SUBMIT_KEY（均带 PROCUREMENT_REVIEW_ 前缀）。调用密钥放运行时 Secret，参考资料由只读挂载提供。生产使用 JOB_TIMEOUT_SECONDS=3600；源码兼容默认1800，支持60至14400秒。旧回调任务可保留 CALLBACK_URL/CALLBACK_KEY 完成交付，新任务不使用它们。

JSON、TXT、CSV 保留原文；XLS/XLSX 复用普通问答的转换器及解析失败策略。原件与已生成伴随文本均记录哈希。固定资料会复制到接口任务的上传目录；这不代表普通页面聊天自动获得这些资料。

Markdown 内容由数字员工负责。后台不检查章节、八项表格、检查项标点、风险或合规结论，也不改写内容；保留原任务及产物归属、路径权限、输入快照完整性、非空 UTF-8 和2MiB传输上限。业务审查要求仍写在提示词中。旧 DOCX 任务维持原验收逻辑。

首次安装仍需0023采购任务表及索引；本次从已安装0023的版本更新不新增迁移。切换旧镜像不能代替数据库兼容检查。

## 本次 Data QA 配置更新

部署工具位于 `deploy/enterprise/procurement-dataqa-20261123/`。它将 datalake_data_qa 指向 `http://10.77.124.142:9080/appforge/openapi/v1/InvokeApp/app-jeg6ud59`，通过 APPFORGE_DATA_QA_API_KEY 引用独立 Secret；inputs 保持 hytoken/hyaccount 空字符串，conversationId 继续由当前用户及线程生成 SquareWorkflow_ 标识。

现有 AppForge 适配器无需修改。工具配置优先于环境变量，因此脚本克隆当前 config.yaml 的 ConfigMap，只替换 Data QA 的 api_url、app_id、conversation_prefix、api_key 环境引用，同时更新四个专用环境项。其余配置、镜像、挂载及数据库保持不变。生产 Bash 80次提醒/100次上限、Retrieve 读取超时240秒等已配置值由克隆保留；这不是将这些值强制应用到所有新安装的默认配置。

脚本是已交付 r4 的受约束升级器，需要其中记录的旧源码和部署前置条件；不是任意版本通用安装程序。用户回执表明 generation 428 已提交，本文不声称本次滚动或新 Data QA 真实查询验收完成。旧失败任务不会因更新自动重跑或改成成功。
