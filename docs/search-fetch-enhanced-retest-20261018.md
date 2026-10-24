# Search / Fetch 增强版实际对比复测

日期：2026-10-18。分支：`codex/search-fetch-comparison`。

本轮验证[DeepSeek 适配器增强](deepseek-search-adapter-enhancements-20261018.md)后的真实调用，历史对照为[统一云端 URL 校验后的复测](search-fetch-cloud-url-retest-20261018.md)。没有切换生产默认工具、提交或部署。

2026-10-19 更新：已补充 [PDF 边界诊断及官方 Harness 复测](deepseek-pdf-fetch-diagnostic-20261019.md)。用户提供的官方 Harness 云端 Fetch 结果与本仓一致：相同 PDF URL 返回 API 404、对应 HTML 成功；PDF request_id 为 `6173e34c-1cae-4c02-9d96-301de65c155c`。此前 Harness 的非公网 DNS 拦截属于另一测试路径，不计入云端 API 对照。这些补充不改变本文 2026-10-18 的样本数、耗时及 Token 统计。

## 测试口径

沿用相同两个脚本、5 个搜索问题、5 个固定网页 URL、每次 5 条搜索结果及 20000 字符正文预算。适配器层经真实工具注册、ToolNode 和结果净化执行，使用各家实际客户端。

Agent 层使用 DeerFlow `make_lead_agent` 及真实中间件链，模型为公网 `https://api.deepseek.com` 的 `deepseek-flash`，关闭思考。每家各做中国海油年报、Python TaskGroup 两题，沿用每题最多 1 次搜索、3 次抓取、6 次模型调用、180 秒限制。这是 Agent 核心链路测试，不包含 Gateway、浏览器或 Kubernetes 验收。

共 30 次适配器工具调用、12 次 Agent 工具调用及 18 次模型调用。未统计供应商 SDK 内部传输重试，不能等同于核对后的账单请求数；未核对实际扣费。

供应商选择口径（2026-10-19 补充）：当前 DeerFlow 由 `tools[].use` 分别固定选择 Search、Fetch 实现，支持两者使用不同供应商。模型只调用统一工具名；配置多个 Key 不会自动选择供应商。启用多个同名工具时，注册按实际工具名保留首个已加载实现并对后续项告警，配置查询也取首个同名配置，不构成自动路由或失败回退。仅改配置 `name` 不能改变适配器内部固定的工具名。本轮脚本为三家分别构造独立 AppConfig，未让模型在三个同名实现间选择。完整规则及源码依据见[接入方案第 6.2 节](web-search-fetch-provider-comparison-20261018.md#62-配置与凭证)。

## 适配器结果

中位耗时仅统计成功请求。

| 服务 | Search 成功 | Search 中位耗时 | Fetch 成功 | Fetch 中位耗时 |
| --- | ---: | ---: | ---: | ---: |
| Tavily | 5/5 | 2.320 秒 | 5/5 | 1.659 秒 |
| DeepSeek | 5/5 | 0.447 秒 | 4/5 | 4.572 秒 |
| Exa | 5/5 | 1.534 秒 | 5/5 | 0.478 秒 |

29/30 次返回有效搜索结果或非空正文。唯一失败仍是 DeepSeek 抓取固定年报 PDF：

`https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf`

耗时 5.364 秒，错误合同正确返回 `DEEPSEEK_PROVIDER_NOT_FOUND`、`error_scope=provider_api`、`http_status=404`、`retryable=false`。这是供应商 API 状态；目标网页真实状态未知，不能推断 DeepSeek 不支持全部 PDF。其余政策、Python、Kubernetes、GitHub 四个 HTML 页面均成功。

本轮样本中 DeepSeek Search 耗时最低、Exa Fetch 耗时最低。测试仍按 Tavily、DeepSeek、Exa 顺序执行，且没有随机交叉、多时段重复或区分页面缓存，因此不能将它们视为总体性能排名。

## Agent 结果与答案核验

| 工具服务 | 中国海油题耗时 | Python 题耗时 | 正文读取与核心答案核验 |
| --- | ---: | ---: | ---: |
| Tavily | 9.653 秒 | 7.750 秒 | 2/2 |
| DeepSeek | 12.427 秒 | 4.171 秒 | 2/2 |
| Exa | 5.176 秒 | 5.058 秒 | 2/2 |

六题均为 3 次模型调用、1 次搜索、1 次抓取，没有工具失败或预算超限。这里的通过指工具轨迹和核心事实核验，不代表所有输出要求都满足，没有双盲质量评分。

- 三家年报答案均给出 726.8 百万桶油当量、同比增长 7.2%，与实际抓取内容一致。
- Tavily、Exa 选择 `https://www.cnoocltd.com/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf`，返回约 20000 字符正文。
- DeepSeek 选择 `https://ltd.cnooc.com.cn/xwzx/gsxw/2025/202503/t20250327_114400.html` 官方年度业绩公告，返回 2775 字符。它没有尝试固定测试中失败的 PDF，不能据此说 PDF 问题已修复。
- 三家 Python 答案覆盖同组任务取消、等待退出及 ExceptionGroup / BaseExceptionGroup，另说明 KeyboardInterrupt / SystemExit 的特殊处理。
- DeepSeek 明确说明 Python 抓取正文在后部截断；Tavily 年报题也承认正文截断。Tavily Python 答案却声称“无证据限制”，而工具结果含截断标记，应改进模型对证据范围的说明。
- 按完整答案 Unicode 字符计数，年报题 Tavily/DeepSeek/Exa 为 262/222/264 字符，Python 题为 466/638/630 字符，均超过题目 200/250 的字数目标。该计数包含链接、英文、标点及格式；只统计汉字则分别为 133/98/109 与 154/247/220。因此不能称为严格字数验收通过，后续应明确统一计数方式。

## 增强行为与模型用量

DeepSeek 五次 Search 共返回 25 条结果：全部标注 `snippet_type=summary`，17 条标注实际截短，20 条保留供应商日期；最长片段恰为配置默认 1000 字符。

五次搜索输出由上一轮 52920 字符变为 27783 字符，减少约 47.5%。两轮并非相同原始响应重放，返回内容也可能变化，这个数字是实际观测差异。

| 服务 | 上轮两题输入 Token | 本轮输入 Token | 本轮输出 Token | 本轮输入中的缓存读取 Token |
| --- | ---: | ---: | ---: | ---: |
| Tavily | 58146 | 58442 | 568 | 57216 |
| DeepSeek | 47770 | 42228 | 624 | 32000 |
| Exa | 56965 | 56965 | 630 | 55931 |
| 本轮合计 | — | 157635 | 1822 | 145147 |

DeepSeek 两题输入 Token 比上轮减少 5542，约 11.6%；中国海油题为 18632、Python 题为 23596。输出 Token 同时由 513 增至 624。不能从这个小样本直接推算费用下降：缓存命中、返回正文、搜索结果和模型措辞都影响账单与耗时。

`ori_question` 的验证边界：适配器脚本只构造 AI 工具调用，没有 HumanMessage，所以按设计不传原始问题；Agent 脚本有真实 HumanMessage，启用默认运行时提取路径。既有真实 ToolNode 单测验证注入、用户消息筛选和 128 字符上限。本轮未抓取出站请求或做关闭该选项的在线 A/B，因此不声称单独测得 `ori_question` 的召回收益，也不把摘要预算收益与该字段收益混为一谈。

本轮没有修改适配器或测试脚本，沿用增强实现阶段的 198 项相关回归结果；本次额外执行真实服务对比及 `git diff --check`。全仓测试已有 Windows `fcntl` 首个失败，见前述报告，未声称全量通过。

## 证据与清理

- 适配器原始脱敏记录：`outputs/search-fetch-adapters-enhanced-20261018/`，含 30 个案例及 `summary.json`。
- Agent 答案、工具轨迹、用量：`outputs/search-fetch-agent-enhanced-20261018/`，6 个案例。
- 扫描本轮目录 37 个文件，未发现三个精确 API 密钥值；临时凭据文件已删除。输出目录被 Git 忽略。

当前结果支持继续将 DeepSeek 作为可选搜索服务接入；其摘要预算和错误合同已在真实请求中生效。若用于本样本涉及的 PDF 抓取，Tavily、Exa 的固定 URL 成功率更好。是否调整默认服务，仍应依据更多样本的随机交叉测试、PDF 覆盖及实际账单。

后续官方 Harness 对照进一步说明，此 PDF 的云端 API 404 并非 DeerFlow 适配器独有问题。API 未提供内部失败阶段，因此不能据此判定源站 404、整个域名不可访问或全部 PDF 不支持；HTML 成功也不能当作 PDF 已成功读取。本报告实测时未实现通用 Fetch 回退；2026-10-24 已新增可选[统一路由适配器](web-provider-routing-20261024.md)，经离线模拟回归验证，尚未使用新路由重跑真实服务对比，不改变本文历史统计。
