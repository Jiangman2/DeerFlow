# DeepSeek 搜索适配器：上下文、来源预算与错误合同

日期：2026-10-18。分支：`codex/search-fetch-comparison`。本次实现前一轮设计讨论确认的三个优先项，没有增加多查询批处理、自动重试或新的 WebRuntime。

## 1. 原始问题

`web_search` 的模型可见参数仍只有 `query`。通过 LangGraph 注入的 ToolRuntime，从真实 HumanMessage 提取触发搜索的用户文本，并作为 `ori_question` 发送。

- 排除隐藏消息、非 user-input 的命名系统恢复消息、AIMessage，以及运行边界标记的旧消息。
- 只提取文字，忽略图片等非文本块；合并空白，按 Unicode 字符限制到 128 字。
- 当前用户消息没有文字时省略字段，不回退到旧问题；没有运行时的独立工具调用也省略。
- `send_original_question: false` 可关闭附带原始问题。

这不是让模型自己填写第二个问题，也没有把整段对话或隐藏持久上下文传给搜索服务。

## 2. 来源输出

成功结果保持原有 JSON 列表及 title/url/snippet，增加可选元数据：

| 字段 | 含义 |
| --- | --- |
| snippet_type | 当前片段来自 summary 或 snippet，保留二者语义区别 |
| snippet_truncated | 仅在片段实际截短时写 true |
| published_at | 原样保留供应商提供的非空 date，不推测日期 |

配置示例已加入 config.example.yaml 的注释段：

```yaml
send_original_question: true
snippet_source: summary
snippet_max_characters: 1000
```

默认仍优先长摘要 summary，缺失时使用 snippet；可配置反向选择。每条片段默认最多 1000 字符，部署值限制在 1～10000。只返回所选片段，不默认同时重复发送长短摘要。可选来源信息缺失时不补造。

注意：默认长度预算是本次新增行为，前面已经完成的真实 API / Agent 耗时与 Token 数据不能当成本版本的新实测结果。

## 3. 错误合同

Search 和 Fetch 共用 `errors.py`，错误载荷包含：

- error：固定、安全的可读说明。
- error_code：稳定的 DEEPSEEK_* 类型。
- retryable：是否适合在条件允许时重试；不自动发起重试。
- error_scope：configuration、client_validation、provider_api、provider_response、transport 或 adapter。
- http_status：仅供应商 API 返回 HTTP 状态时提供。

Search 返回 JSON 错误；Fetch 为保持现有合同保留 `Error: ` 前缀，后接同样的 JSON 载荷。

主要类型包括凭证缺失、配置无效、请求无效、认证、余额、访问拒绝、供应商 404、限流、不可用、超时、网络错误、非法响应、响应过大和空正文。

404 明确为供应商 API 状态，不返回或推测 target_http_status。网络异常和上游错误正文均不原样回传，避免泄露凭证、私有诊断或不可信服务文本。取消继续向外传播，不包装成普通失败。

现有工具结果中间件仍可识别 error 字段/前缀并归类，例如 HTTP 429 归为 rate_limited；本次没有改动宿主全局重试策略，也没有让模型通过参数修改 endpoint 或预算。

## 4. 验证

先编写测试复现缺口：新增集合最初 15 failed、1 passed。实现后运行新旧适配器、Tavily、结果净化和输出预算集合，结果 **198 passed**。

覆盖真实工具 schema、ToolNode 的运行时注入、多模态文字提取、隐藏/旧消息排除、配置关闭、片段选择和截短、可选日期、401/402/429/503、Fetch API 404、超时脱敏、宿主中间件识别和取消传播。

Ruff、格式和 YAML 检查通过。全量后端入口仍使用 -x 在首个失败停止；已知 Windows 上渠道模块 import fcntl 的失败不在本次修改范围，不声明全仓通过。

实现阶段没有调用付费 API、没有重新使用密钥，也没有部署或切换默认工具。用户随后授权的真实服务复测已完成，见[增强版实际对比复测](search-fetch-enhanced-retest-20261018.md)；该复测不是单独隔离 ori_question 收益的在线 A/B。

## 文件

- `backend/packages/harness/deerflow/community/deepseek/tools.py`
- `backend/packages/harness/deerflow/community/deepseek/errors.py`
- `backend/tests/test_deepseek_search_context.py`
- `backend/tests/test_search_fetch_adapters.py`
- `config.example.yaml`
