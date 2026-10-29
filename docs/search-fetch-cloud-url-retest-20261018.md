# 统一云端 URL 校验后的 Search / Fetch 复测

日期：2026-10-18。分支：`codex/search-fetch-comparison`。本报告替代[上一轮](search-fetch-adapter-agent-results-20261018.md)受 DeepSeek 本地 DNS 拦截影响的 Fetch / Agent 比较结论。

## 修改

先补充失败回归，再实现 `validate_cloud_fetch_url`，Tavily、Exa、DeepSeek 三家云端 Fetch 均在调用供应商前使用它：

- 检查 HTTP(S)、完整主机、URL 长度、端口和凭据，不接受非法 URL。
- 拒绝 localhost、显式本地域名、非公网 IP 字面量及数字编码回环地址。
- **不调用本机 DNS 来判断远端云服务会访问哪个地址。** 实际网页的域名解析、目标访问及重定向策略由云服务端执行；此检查不能代替供应商服务端的目标安全策略。
- DeepSeek 仍只连接固定官方 API 主机，不跟随 API 重定向，保留超时、响应大小限制及错误脱敏。
- 现有本地/自建抓取使用的 `resolve_host_addresses`、`is_blocked_address`、`validate_public_http_url` 与 HEAD 的函数 AST 一致，没有改动原有 DNS 检查。

测试继续使用公网 `https://api.deepseek.com`，模型为 `deepseek-flash` 非思考模式；没有使用内部模型地址，也没有切换生产配置。

## 1. 适配器层：30 次真实调用

沿用相同的 5 个查询和 5 个固定 URL、各家相同结果数、正文 20000 字符预算，通过真实 AppConfig、DeerFlow 工具注册、LangGraph ToolNode、结果净化和供应商客户端执行。

| 工具服务 | Search 成功 | 成功请求中位耗时 | Fetch 成功 | 成功请求中位耗时 |
| --- | ---: | ---: | ---: | ---: |
| Tavily | 5/5 | 2.150 秒 | 5/5 | 1.616 秒 |
| DeepSeek | 5/5 | 0.430 秒 | 4/5 | 4.694 秒 |
| Exa | 5/5 | 2.139 秒 | 5/5 | 0.484 秒 |

DeepSeek 不再被本地 DNS 拒绝。唯一失败为固定年报 URL：

`https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf`

这次真实调用上游后返回 `Error: DeepSeek HTTP 404`，耗时 5.266 秒。该错误应归为“上游未能获取指定 URL”，不能解释成 DeepSeek 完全不支持 PDF。政策 HTML、Python 文档、Kubernetes 文档、GitHub 首页四页均成功。

这轮 30 个适配器尝试全部进入供应商客户端，其中 29 个返回有效搜索结果或非空正文。成功仍不等于全文完整或事实全部准确。

## 2. Agent 层：六个真实任务

仍使用真实 DeerFlow `make_lead_agent` 和原有中间件链；每个任务最多 1 次 Search、3 次 Fetch、6 次模型开始请求、180 秒。题目、模型、模式和预算与上一轮相同。

| 工具服务 | 中国海油年报题 | Python TaskGroup 题 | 正文核验完成 |
| --- | ---: | ---: | ---: |
| Tavily | 12.673 秒 | 8.229 秒 | 2/2 |
| DeepSeek | 15.633 秒 | 4.841 秒 | 2/2 |
| Exa | 5.394 秒 | 5.895 秒 | 2/2 |

六个任务均为 3 次模型调用、1 次 Search、1 次 Fetch，没有工具错误，也没有达到预算上限。

- 三家年报题均给出净产量 726.8 百万桶油当量、同比增长 7.2%，有官方来源支持。
- Tavily、Exa 选择并读取 `www.cnoocltd.com` 的官方年报 PDF。
- DeepSeek 选择并读取 `ltd.cnooc.com.cn` 的官方年度业绩 HTML 公告，返回给模型的正文约 2775 字符。这个任务没有抓取适配器固定测试中的 PDF，因此两项结果不矛盾，不能据 Agent 成功认定该 PDF 404 已解决。
- 三家 Python 题均读取了用户明确给定的官方文档，回答覆盖同组任务取消、异常组和特殊异常。DeepSeek 本次说明了后续章节被截断；所有输出仍需进一步检查字数限制、完整阅读范围和引用格式。

“完成”基于实际工具轨迹及核心答案的人工抽查，没有双盲质量评分。这里验证的是 Agent 核心链路，不包含浏览器、Gateway 登录或 Kubernetes 部署验收。

## 3. 模型用量

| 工具服务 | 两题合计输入 Token | 输出 Token | 输入中的缓存读取 Token |
| --- | ---: | ---: | ---: |
| Tavily | 58146 | 585 | 43904 |
| DeepSeek | 47770 | 513 | 40192 |
| Exa | 56965 | 673 | 43520 |
| 合计 | 162881 | 1771 | 127616 |

数据来自 18 次真实模型响应的 usage_metadata。Agent 层另有 12 次工具客户端调用。本轮适配器与 Agent 合计 42 次工具客户端调用；未抓包统计 SDK 内部传输重试，不将它当作核对后的账单请求数。未核对实际扣费。

所有服务按 Tavily、DeepSeek、Exa 顺序执行，模型前缀缓存、供应商页面缓存、返回内容长度和来源选择不同。不能把这次耗时表作为总体性能排名；下一轮正式性能评测应随机交叉、多时段重复，并区分缓存与新抓取。

## 4. 回归验证

- 新增三家共同 URL 规则、非法端口/协议/凭据/回环地址、以及“公共主机不依赖本地 DNS”的测试。修复前新增测试有 15 项失败，修复后相关集合 **179 passed**。
- 额外运行 Browserless 与 Crawl4AI 旧测试：47 passed、4 failed。4 项均是 Crawl4AI 成功路径测试被本机合成 DNS 地址拒绝；本次没有修改这些本地抓取函数，也没有为了让测试通过而解除其 DNS 检查。这是保留下来的环境限制，不宣称整个抓取测试集合全绿。
- 全量后端入口再次以 `-x` 运行：1 failed、5 passed、3 skipped，首个失败仍是渠道配置模块在 Windows 上导入 Unix `fcntl` 失败，见前轮报告。全仓回归仍未通过。
- Ruff 检查、格式检查、YAML 示例解析及 `git diff --check` 验证；没有更新依赖或修改其他业务模块。

## 5. 记录与清理

- 适配器证据：`outputs/search-fetch-adapters-cloud-url-20261018/`。
- Agent 答案、工具轨迹和 Token：`outputs/search-fetch-agent-cloud-url-20261018/`。
- 全量首个失败：`outputs/search-fetch-cloud-url-full-first.log`。
- 可复用脚本仍是 `scripts/benchmark_search_fetch_adapters.py` 与 `scripts/benchmark_search_fetch_agent.py`。

输出目录被 Git 忽略。逐文件扫描三个精确密钥值，未发现泄漏；临时密钥文件已删除。代码、报告和生产配置没有写入密钥，未提交、推送或部署。

本轮已验证原先的误拦截修复，三家均能完成两道真实 Agent 任务。当前保留的具体差异是：DeepSeek 对固定年报 PDF URL 返回 404；更多问题覆盖、缓存公平性及费用比较仍需更大样本。
