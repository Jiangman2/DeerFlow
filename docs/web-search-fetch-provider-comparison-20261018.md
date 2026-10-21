# Tavily、DeepSeek、Exa Search / Fetch 对比与接入方案

核对日期：2026-10-18。本文是设计与评测方案，编写时未调用付费 API，未修改生产配置。功能和价格来自官方文档；质量排序须经实测。后续已完成首轮 30 次真实调用，见 [实测报告](search-fetch-smoke-results-20261018.md)，其结果不代表生产配置已切换。

实测进展更新（2026-10-19）：最新三家 Search / Fetch 与真实 Agent 对比见[增强版复测报告](search-fetch-enhanced-retest-20261018.md)；PDF 失败边界及用户提供的官方 Harness 复测见[专项诊断](deepseek-pdf-fetch-diagnostic-20261019.md)。本文原有价格与功能核对日期仍为 2026-10-18，本次只补充实测证据，不表示重新核对了价格。

## 1. 建议

三家都能够组成“搜索发现 → 正文阅读”链路。建议保留 Tavily 为现有基线，补齐 DeepSeek 双工具，以 Exa 为另一组候选；先按部署配置选择 Search 与 Fetch，再根据评测决定默认组合。第一阶段不引入按问题自动路由或静默跨供应商回退。

定位判断：Tavily 的搜索与提取参数较丰富；DeepSeek 的独立 JSON 接口和计费简单，值得验证中文业务适配；Exa 提供语义检索及细粒度内容、缓存新鲜度控制。以上不是中文质量、稳定性或速度排名。

## 2. Search 功能

| 维度 | Tavily | DeepSeek | Exa |
| --- | --- | --- | --- |
| HTTP 接口 | POST https://api.tavily.com/search | POST https://api.deepseek.com/v1/tools/search | POST https://api.exa.ai/search |
| 鉴权 | Bearer API Key | Bearer API Key | x-api-key；也支持 Bearer |
| 检索配置 | basic / advanced / fast / ultra-fast | query、count、可选原始问题与本地化 IP | auto / instant / fast，以及 deep 系列 |
| 站点和日期 | 结构化域名、日期参数 | site:、-site:、after:、before: 等查询语法 | includeDomains / excludeDomains、发布日期参数 |
| 语言/地域 | 提供语言与地域相关参数 | lang: 语法、localization | userLocation 等配置；按选定模式核对限制 |
| 正文与片段 | content 为相关内容片段；可选 raw_content | snippet、summary；need_content 可取正文 | 可选 text、highlights、summary |
| 元数据 | URL、标题、score、可选发布日期等 | URL、标题、rerank_score、authority、日期等 | URL、标题、发布日期、作者等 |
| 首轮建议 | basic，5 条，关闭 answer 和 raw_content | count=5，need_content=false | auto，5 条，highlights |

注意：三家的分数定义不同，不能直接合并排序；摘要、相关片段、完整正文须区别标记。Exa 部分分类不支持部分日期/域名条件；统一层遇到不支持的过滤条件应明确报错，不可静默忽略。

参考：[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[DeepSeek Search / Fetch](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Exa Search](https://exa.ai/docs/reference/search)。

## 3. Fetch 功能

| 维度 | Tavily Extract | DeepSeek Fetch | Exa Contents |
| --- | --- | --- | --- |
| HTTP 接口 | POST https://api.tavily.com/extract | POST https://api.deepseek.com/v1/tools/fetch | POST https://api.exa.ai/contents |
| URL 输入 | 支持多 URL | 单 URL | 支持多 URL / 文档 ID |
| 输出 | raw_content；Markdown 或 text | title + Markdown content | text、highlights、summary 等可选内容 |
| 提取控制 | basic / advanced；可按 query 选相关块 | 当前文档只暴露 URL | 内容长度、新鲜度、实时抓取超时等 |
| 新鲜度 | 不假设具备与 Exa 等价的缓存开关 | 未公开同类参数 | maxAgeHours=0 请求新抓取；-1 仅缓存 |
| 失败信息 | failed_results，支持部分成功 | HTTP 错误 | 每 URL 状态信息 |
| DeerFlow 首期 | 仍采用单 URL，关闭 query 分块保证正文评测 | 单 URL | 单 URL，text，明确记录新鲜度模式 |

不预设任何一家能绕过登录、验证码或访问限制。复杂页面、表格、长文、PDF 应分别测成功率和完整度；“返回 HTTP 200”不等于获取了有效正文。

PDF 实测补充：DeepSeek 对 4 个 PDF URL（3 个不同文件）均返回供应商 API 404，对照的 3 个 HTML URL 均成功；源站直接探测均为 200。Tavily、Exa 均能读取两个官方域名下字节相同的年报 PDF。用户在官方 Harness 使用同一云端端点复现了原 PDF 404、对应 HTML 成功，说明此失败不是 DeerFlow 适配器独有问题。该结论只覆盖已测样本，尚不确定服务内部根因，也不能推广为全部 PDF 不支持。

参考：[Tavily Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract)、[DeepSeek 指南](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Exa Contents](https://exa.ai/docs/reference/get-contents)。

## 4. 成本口径

按当前官方公开按量价格，排除免费额度、套餐折扣、税费、模型 Token 和汇率变化：

| 项目 | Tavily | DeepSeek | Exa |
| --- | --- | --- | --- |
| 标准搜索 1000 次 | basic 1000 credits，按 $0.008/credit 为 $8 | 有结果的成功请求 ¥5 | auto / fast $7；instant $4，基础包含最多 10 条 |
| 正文提取 1000 页 | basic 200 credits，约 $1.60；advanced 约 $3.20 | 成功请求 ¥5（单页一次） | text 单内容类型 $1；多内容类型分别计费 |
| 高级搜索 | advanced 为标准 credit 的两倍 | 当前无深度档位参数 | deep 系列另计费 |

示例：1000 个任务，每任务搜索一次、再抓取 3 页，全部成功且无重试。Tavily basic 约 $12.80；DeepSeek ¥20；Exa auto + text 约 $10。此为按文档单价计算，不是账单承诺；Tavily 零散 URL 提取的记账/舍入口径需在试跑核对，Exa 搜索超过基础条数、生成摘要或获取多个内容类型会增加费用。

DeepSeek Search 与 Fetch 当前分别为账号级 5 QPS，需分别限速。另两家按实际账户档位核定，不假设无限并发。

参考：[Tavily 价格](https://docs.tavily.com/documentation/api-credits)、[DeepSeek 计费与限速](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Exa 价格](https://exa.ai/docs/admin/pricing)。

## 5. DeerFlow 现状与差距

源码：`backend/packages/harness/deerflow/community/{tavily,exa}/tools.py`。

| 项目 | 现状 | 需要处理 |
| --- | --- | --- |
| Tavily Search | 已实现 query + max_results，并归一为 title/url/snippet | 按需增加官方支持的配置；示例 include_domains / exclude_domains 尚未在当前函数透传 |
| Tavily Fetch | 已实现 extract，但示例未列出 | 客户端当前读取 web_search 配置，需改为按工具读取；不能假设 Extract 返回 title；补示例配置 |
| Exa Search | 已实现 auto + highlights，可配置结果数和片段长度 | 示例仍列 neural / keyword 等旧选项，需按当前 API 与安装 SDK 核对更新 |
| Exa Fetch | 已实现 get_contents(text) | 暴露必要的长度与新鲜度配置 |
| DeepSeek | 未发现独立 Search / Fetch 适配 | 新增双工具与配置、模拟测试 |
| 正文长度 | Tavily / Exa 当前都只返回前 4096 字符 | 改为统一可配置预算，标记截断；避免误判供应商正文能力 |

更正前面对话：Tavily Fetch 已在源码实现，缺的是示例配置及部分适配完善。

## 6. 接入设计

### 6.1 保持现有工具入口

模型继续调用 `web_search(query)` 与 `web_fetch(url)`。三家作为可替换的 provider，通过现有 tools[].use 按工具选择，每个工具名只注册一个实现。允许 DeepSeek Search + Tavily Fetch 等混合组合。

第一期不增加第三套对话模型协议，不改 PPT Agent Host 或企业 Responses 中继。官方 DeepSeek Search / Fetch 是独立服务；内部模型网关是否透传这两个路径必须另行核验，不能沿用兼容 Chat Completions 的推断。

### 6.2 配置与凭证

供应商选择规则（2026-10-19 按当前分支源码核对）：

| 配置情形 | 当前实际行为 |
| --- | --- |
| 每个工具名仅配置一个实现 | `tools[].use` 固定决定供应商；模型只选择调用 Search 或 Fetch |
| Search 与 Fetch 来自不同供应商 | 支持，例如 DeepSeek Search + Exa Fetch，各自读取对应工具配置 |
| 仅设置多家 API Key | 不会自动注册、选择或轮换供应商 |
| 同时启用多个 `name: web_search` 或多个 `name: web_fetch` | 工具注册按实际工具名去重，保留当前加载顺序中的第一项，后续同名项跳过并告警；同名配置查询也取配置列表中的第一项 |
| 某供应商调用失败 | 当前没有通用的跨供应商自动回退，也没有按语言、URL 类型、价格、延迟或成功率自动路由 |
| 仅将配置 `name` 改成供应商专用名称 | 不能直接实现多供应商并存：现有适配器内部工具名仍固定为 `web_search` / `web_fetch`，配置名与实际工具名不一致会告警，绑定使用实际工具名；适配器也按固定工具名查参数与凭据 |

代码依据：[`get_available_tools`](../backend/packages/harness/deerflow/tools/tools.py) 先按工具组筛选并加载配置，再按工具对象的 `.name` 保留首项；[`AppConfig._build_name_indexes` 与 `get_tool_config`](../backend/packages/harness/deerflow/config/app_config.py) 用 `setdefault` 保留首个同名配置。正常情况下应让配置名、工具名一致，并为 Search 和 Fetch 各保留一个有效配置。重复条目不是供应商候选池，也不构成失败备用列表；不要借不同工具组中的同名条目实现路由，因为参数查询仍按全局同名配置首项进行。

本次三家对比脚本为每个供应商分别构造独立 AppConfig 后执行，未在同一 Agent 中同时启用三个同名实现。若后续实现多供应商路由或可选回退，需要新增明确的选择、凭据隔离、预算及调用来源记录机制；它不是当前已有行为。这里描述的是 DeerFlow 的配置机制，不是官方 DeepSeek Harness 的 provider 注册机制。

分别使用 TAVILY_API_KEY、DEEPSEEK_API_KEY、EXA_API_KEY；运行时 Secret 注入，不写字面密钥。每个工具独立读取 api_key、timeout、结果/正文预算等配置。代理和 CA 按现有企业部署显式配置，保持证书链及主机名验证。

拟新增 DeepSeek 路径（尚未实现）：`deerflow.community.deepseek.tools:web_search_tool` 与 `web_fetch_tool`。生产仅允许管理员配置 API 地址，禁用 HTTP 重定向，限制响应体与总耗时，日志不记录密钥或完整业务问题。Fetch 仅接收合法 HTTP(S) 公网 URL，拒绝凭证 URL、回环和私网目标；公共抓取工具不承担内部资料读取。

### 6.3 输出与预算

第一期保持现有 Search 列表的 title/url/snippet 合同，供应商差异与计费信息写受控诊断元数据，避免扩大前端改造。DeepSeek 优先将 summary 映射到 snippet，Exa 使用 highlights，Tavily 使用 content；保留它们的类型差异用于评测。

Fetch 返回 URL、可用标题、正文和明确截断说明。建议初始模型正文预算为每页 20000 字符，总响应设独立硬上限，仍服从现有工具输出预算；这是拟定部署值，需按上下文预算校准。原始正文仅在评测受控目录保留，用于完整度评分，不绕开运行时预算。

错误应区分鉴权、余额、限流、超时、目标页失败、空正文、格式不合法。408/429/部分 5xx 才有限重试并遵循 Retry-After，401/402 不自动重试；第一期不跨服务静默回退。沿用 web_search/web_fetch 远程内容净化链。

## 7. 公平评测方案

### 7.1 三项测试分开做

1. **Search：** 60 个固定问题，三家同一时间窗、相同 Top 5。不超过 DeepSeek 的 128 字查询上限；长问题先由统一规则转换成相同短查询。关闭供应商生成答案，站点/时间过滤按各家语法等价设置。
2. **Fetch：** 60 个固定公共 URL，三家抓取相同页面。正文上限相同；评分用受控保存的较完整原文，另测实际送入模型的截断版本。缓存命中/强制刷新分开报告，不能把缓存响应与新抓取混作一个速度指标。
3. **端到端：** 同一个模型、提示词、最多 1 次搜索 + 3 次抓取、相同上下文预算。先测三组同厂商组合，再测最优 Search × 最优 Fetch 的混合组合。

问题组成：中文政策/权威公告 15、中文能源与海工技术 15、英文论文与开源文档 15、近 7 天新闻 10、站点/日期过滤 5。网页包含普通文章、长文、表格、动态页面、已更新页面，以及少量 PDF、不可用页作为边界用例。所有样本冻结日期与来源；两名评审盲评并处理分歧。

### 7.2 指标

| 环节 | 指标 |
| --- | --- |
| Search | Precision@5（相关条数/5）、前 5 条是否命中预先标注的权威来源、重复率、时间/站点约束符合率 |
| Fetch | 有效正文成功率、关键事实/表格保留率、正文完整度、导航广告噪声占比 |
| 端到端 | 答案事实准确率、引用是否支持结论、任务完成率 |
| 工程 | p50/p95 总延迟、首试与重试后成功率、限流率、平均请求数 |
| 成本 | 实际 API 消耗 + 模型输入 Token；换算为每个成功任务成本 |

供应商原始分数不参与跨供应商质量排名。每组重复 3 轮，分散在至少两个时段；固定 URL 测试和搜索后 URL 测试分别统计。早停的大规模故障应保留记录，不用重复重试制造成功率。

拟定验收目标（不是实测结果）：常规 HTML 有效正文成功率 ≥95%，搜索站点约束符合率 ≥95%，整体答案质量不低于现有基线；若样本差异很小，不宣称质量胜出。PDF 等边界类型单列，不把不同内容类别混成一个成功率。

### 7.3 测试规模与预算

每轮最多 180 次 Search 对比调用、180 次固定 URL Fetch，以及 60 个端到端任务 × 3 组合的调用；三轮再按实际请求和重试汇总。实施前按最新单价计算预算上限并设置账户额度，免费额度只作为抵扣，不进入长期成本模型。

## 8. 交付阶段

1. **适配对齐：** 修复 Tavily 配置隔离和可选标题处理，统一正文预算，新增 DeepSeek，更新 Exa 参数说明。针对字段缺失、超长响应、空结果、非法 URL、鉴权、限流、超时、配置隔离编写有意义的模拟测试。
2. **小规模连通性：** 在实际 Gateway 网络和 CA 环境测试三家；确认账户限额、DeepSeek 独立工具路径、可观测性和错误脱敏。
3. **离线评测：** 输出逐样本结果、汇总表、成本账单对照和失败原因；选出 Search 和 Fetch 的默认组合。
4. **灰度：** 先对管理员/测试账号启用；监控成功率、p95 和任务成本。保留旧配置可回滚，通过后再扩大范围。

本次方案不直接切换线上服务。尚待实测确认：中文覆盖与权威来源召回、企业网络可用性、动态页与 PDF 成功率、SDK 对最新参数的兼容性、实际记账及数据处理条款。
