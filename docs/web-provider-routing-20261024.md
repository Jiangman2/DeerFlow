# DeepSeek 优先的 Search / Fetch 路由适配器

日期：2026-10-24。分支：`codex/search-fetch-comparison`。

新增 `deerflow.community.web_router.tools`，以一个 `web_search` 和一个 `web_fetch` 工具封装多供应商顺序选择及失败回退。原有单供应商适配器不变，生产配置和示例中的当前有效 DDGS / Jina 默认项没有切换。

## 默认规则

| 操作 | 默认供应商顺序 |
| --- | --- |
| Search | DeepSeek → Tavily → Exa |
| 普通 URL Fetch | DeepSeek → Tavily → Exa |
| 显式 PDF URL Fetch | Tavily → Exa，跳过 DeepSeek |

PDF 检测仅依据 URL 解码后的 path 是否以 `.pdf` 结尾，忽略大小写、query、fragment；不改写 URL、不提前请求源站。无扩展名 PDF 不能预判，会先按普通 URL 请求，再按失败规则回退。PDF 路由依据此前 [PDF 实测](deepseek-pdf-fetch-diagnostic-20261019.md)，并不是宣称 DeepSeek 永远不能读取任何 PDF。

这是一套确定性的配置规则，不使用模型判定供应商，也不宣称具备相关性评分、语言路由、健康度学习、熔断器或跨请求限流。

## 启用配置

在实际 `config.yaml` 的 `tools` 下，以以下两项替换现有 Search / Fetch 条目。**各工具名只保留一项，不能同时启用旧的同名适配器。** `config.example.yaml` 已提供相同的注释示例，未直接启用。

```yaml
tools:
  - name: web_search
    group: web
    use: deerflow.community.web_router.tools:web_search_tool
    provider_order: [deepseek, tavily, exa]
    max_results: 5
    snippet_max_characters: 1000
    send_original_question: true
    fallback_on_empty: false
    max_attempts: 3
    timeout: 15
    total_timeout: 40
    providers:
      deepseek: {enabled: true}
      tavily: {enabled: true}
      exa: {enabled: true}

  - name: web_fetch
    group: web
    use: deerflow.community.web_router.tools:web_fetch_tool
    provider_order: [deepseek, tavily, exa]
    pdf_provider_order: [tavily, exa]
    max_characters: 20000
    max_attempts: 3
    timeout: 15
    total_timeout: 40
    providers:
      deepseek: {enabled: true}
      tavily: {enabled: true}
      exa: {enabled: true}
```

凭据默认在每次调用时从 `DEEPSEEK_API_KEY`、`TAVILY_API_KEY`、`EXA_API_KEY` 环境变量读取，不存在的 Key 自动跳过。也可在某个工具自己的 `providers.<供应商>.api_key` 覆盖，Search 和 Fetch 配置互相独立；显式空字符串表示不使用该供应商凭据。`enabled: false` 禁用该供应商，即使环境有 Key 也不会调用。

无需把未提供的 Key 写成 YAML `$ENV_VAR`：现有配置加载器会在该变量缺失时拒绝加载，尚未到路由跳过阶段。优先采用上例中的环境变量运行时读取方式。不要在配置中写真实密钥。

顺序列表允许三家中的任意非空、无重复子集。`pdf_provider_order` 是独立顺序，配置普通 URL 的顺序不会自动改变 PDF 顺序；关闭备用供应商应同时检查顺序和 `enabled`。默认 PDF 列表中若两家均无凭据，返回带跳过记录的错误，不会擅自尝试列表之外的 DeepSeek；需要时由管理员显式覆盖 PDF 列表。

配置范围：`timeout` 1～60 秒，`total_timeout` 1～120 秒，`max_attempts` 1～3，`max_results` 1～20，片段预算 1～10000 字符，正文预算 1～100000 字符。非法顺序、预算或供应商配置在调用前返回 `WEB_ROUTER_CONFIG_ERROR`，不产生远端请求。

## 回退与停止条件

- 缺失凭据或禁用：记为 skipped，不消耗远端尝试次数。
- 超时、网络错误、API 401/402/404/408/429/5xx、响应超限、无效响应、空 Fetch 正文：在预算内换下一家，每家最多一次；没有同供应商重试、并行扇出或等待退避。
- API 400/403/413 或 API 重定向：停止该次工具调用，不通过换供应商绕过请求或安全拒绝。
- 非法查询、非法 URL、显式本地/私网目标：在所有远端请求之前拒绝，URL 规则复用云端 Fetch 校验，不引入本机 DNS 误判。
- Search 空数组：默认是正常的无匹配结果，返回 `[]` 并结束；只有显式 `fallback_on_empty: true` 才继续尝试备用服务。
- 每次最多 3 个远端尝试，达到 `max_attempts` 或总时间预算后停止，不再派发下一家。原生异步请求以剩余总预算与单次预算的较小值设置 `asyncio.timeout`，同时设置 HTTPX 超时及流读取检查；取消向外传播并中断当前异步请求，不转成普通错误继续回退。

HTTP 状态在记录中命名为 `provider_http_status`，始终指供应商 API 响应，不推测网页源站状态。认证与余额失败会记录固定错误码并允许使用备用服务；运维应修复或禁用长期失败的凭据，本版本没有跨调用熔断缓存。

## 输出与调用来源

- 模型参数仍仅有 `web_search(query)`、`web_fetch(url)`，不能修改供应商、端点或预算。
- Search 成功保持 JSON 列表与 `title` / `url` / `snippet`，每条增加 `provider`，保留实际片段类型和截短标记；DeepSeek 优先 summary，原始用户问题提取沿用既有过滤及 128 字符上限。
- Fetch 成功保持 Markdown，显示正文截断说明，并附 `[Fetch provider: ...]`。
- `ToolMessage.artifact.web_routing` 记录实际供应商、路由类型和各次 skipped / failed / success、固定错误码、供应商 HTTP 状态及远端尝试耗时。跳过项没有远端耗时。这些 artifact 不自动变成模型消息正文，也不代表已有专用前端路由面板。
- 全部失败返回既有中间件可识别的 JSON `error`（Fetch 保留 `Error: ` 前缀），并保留脱敏尝试轨迹。上游错误正文、密钥、URL、查询和原始问题不写路由诊断或路由日志。

三家调用均使用固定官方端点的 HTTPX 异步客户端，`trust_env=false`，禁用 API 重定向与自动重试，每个响应解码后最多 2 MiB。没有复用原单供应商 SDK 的隐式重试或全局代理配置。Tavily 固定 basic 搜索/提取，Exa 固定 auto + highlights 搜索及 text 提取，避免回退时隐式切到高价模式。此新客户端路径不继承旧 Tavily 的可选企业 TLS 兼容 Session；需要该 Session 的部署应继续用原适配器或另行提供显式、受控的客户端配置。

## 验证与状态

57 项新增测试与既有 Search / Fetch、Tavily、结果净化和输出预算回归合计 **294 passed**。覆盖三家顺序、PDF query/大小写/编码、独立配置与并发凭据隔离、HTTP 错误、缺失/禁用凭据、空结果、异常响应、2 MiB 响应限制、总尝试预算、真实异步取消/超时、ToolNode 原始问题注入、schema、净化及 artifact 保留。

没有使用真实密钥或调用付费 API；此前的延迟、质量和 Token 测量属于旧单供应商路径，不能作为新路由器的线上实测结果。本次不宣称全仓回归通过，不提交、推送、部署或改变生产供应商。需要联网验收时，应单独记录主服务成功、注入主服务失败后的回退、PDF 自动路由三种路径与实际请求数。
