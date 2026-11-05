# Search / Fetch：DeerFlow 适配器与真实 Agent 测试

日期：2026-10-18。分支：`codex/search-fetch-comparison`。前序结果见 [官方 API 直连测试](search-fetch-smoke-results-20261018.md)。

更新：本报告中的 DeepSeek 本地 DNS 误拦截已修复；受影响的 Fetch / Agent 对比结论以[统一校验后的复测报告](search-fetch-cloud-url-retest-20261018.md)为准。下文保留作为历史测试记录。

## 1. 已完成的验证

本轮分两层，均没有修改或部署线上配置：

1. **适配器集成：** 真实 `AppConfig → get_available_tools → 编译的 LangGraph ToolNode → ToolResultSanitizationMiddleware`，调用各家仓库工具函数与实际 SDK/HTTP 客户端。每家 5 个相同查询、5 个固定 URL。
2. **真实 Agent：** 使用 DeerFlow 自己的 `make_lead_agent`、模型工厂、系统提示词、完整中间件构造和工具注册；模型为官方 `deepseek-flash`，非思考模式。每家两道相同研究题，实际让模型选择工具、阅读结果并生成答案，没有假模型或人工填充答案。

第二层验证了 Agent 核心执行链，不包含浏览器、Nginx、Gateway 登录、部署级持久化或 Kubernetes Sandbox。测试关闭记忆、自动标题、Skill 发现和历史压缩，使用独立运行目录；没有注册文件写入或 Shell 工具，原有安全中间件保留。

## 2. 代码改动

- 新增 `community/deepseek/tools.py`：独立 Search / Fetch 适配器，固定官方 API 主机；按工具隔离凭证与配置，限制查询长度、响应大小及网络超时，不跟随重定向、不读取环境代理，不把上游异常正文或密钥带回模型。
- 修复 Tavily Fetch 误读 `web_search` 配置的问题；缺少可选标题时采用回退标题。
- Tavily、Exa、DeepSeek Fetch 共用可配置正文预算；默认维持 4096 字符兼容性，本轮统一设置为 20000。客户端截断时添加标记。Exa 还会在服务端应用请求上限，不保证返回截断标记。
- 为 Tavily Fetch、DeepSeek 双工具补充注释配置示例，不更改当前默认服务。
- 新增适配器和 Agent 对比脚本、配置/异常/安全/调用预算回归测试。

## 3. 适配器实测

| 服务 | Search 成功 | 成功请求中位耗时 | Fetch 成功 | 成功请求中位耗时 |
| --- | ---: | ---: | ---: | ---: |
| Tavily | 5/5 | 2.300 秒 | 5/5 | 1.829 秒 |
| DeepSeek | 5/5 | 0.511 秒 | 0/5：本地拦截 | 不适用 |
| Exa | 5/5 | 1.445 秒 | 5/5 | 0.464 秒 |

Search 成功表示工具返回可解析列表且有 title/url/snippet；Fetch 成功表示工具返回非空页面正文。这不是全文完整度或事实准确率。

### DeepSeek Fetch 的本地阻塞

5 个 Fetch 请求全部在调用上游前被 `validate_public_http_url` 拒绝。正常本机进程的 DNS 诊断发现，测试域名被解析到非公网地址，例如：

| 域名 | 本机返回地址 |
| --- | --- |
| www.nea.gov.cn | 198.18.0.160 / 2001:2::88 |
| docs.python.org | 198.18.0.162 / 2001:2::8a |
| github.com | 198.18.0.99 / 2001:2::4e |

这些地址均不通过公共地址检查。它与 DNS 代理的合成地址模式相符，但本次没有查看或修改代理配置，不能断言具体软件来源。

**这不是 DeepSeek Fetch 上游 API 的成功率为 0。** 当前新增 DeepSeek 适配器使用本地 DNS 公网校验，而现有 Tavily/Exa 云端适配器未使用该检查，形成了测试路径差异。保留安全拒绝，未通过允许私网、伪造解析结果等方式绕过；需在真实公网 DNS 环境复测，才能比较三家的适配器 Fetch 上游表现。该阻塞必须解决并验证后，才能把此适配器作为本环境的生产默认 Fetch。

### Search 的实际差异

- DeepSeek 继续快速返回结果；Python 文档查询前两条为第三方包和波斯语镜像，目标官方文档未出现在前五条。
- Exa 的中文年报首条为公司官方 PDF；Python 首条为 docs.python.org；DeerFlow 架构首条为上游架构文件。
- Tavily 本次中文年报查询成功，首条为国资委业绩报道；Python 首条为官方文档；DeerFlow 查询结果包括上游 README 和架构讨论。

搜索响应会变化，这些只是本轮返回结果的检查，不是总体能力排名。

## 4. 真实 Agent 任务

模型统一为 `deepseek-flash`，工具组分别采用三家配对。两个任务为：

1. 核实中国海油 2024 年油气净产量、同比增幅，搜索固定查询词后读取官方来源并引用。
2. 依据 Python 官方文档解释 TaskGroup 首个非取消异常后的同组任务和异常传播行为；固定查询词，官方文档 URL 由测试问题明确提供。

每个任务预算：最多 1 次 Search、3 次 Fetch、6 次模型开始请求、180 秒。通过 `raise_error=True` 的测试回调在执行前限制额外请求，原图中间件、递归和循环保护继续生效。实际无任务触及上限。

| 工具提供方 | 任务 | Agent 耗时 | 模型调用 | Search / Fetch 次数 | 是否完成正文核验 |
| --- | --- | ---: | ---: | ---: | --- |
| Tavily | 中国海油年报 | 10.584 秒 | 3 | 1 / 1 | 是，官方 PDF |
| Tavily | Python TaskGroup | 8.072 秒 | 3 | 1 / 1 | 是，官方文档 |
| DeepSeek | 中国海油年报 | 5.792 秒 | 4 | 1 / 2 | 否，两次 Fetch 均本地拒绝 |
| DeepSeek | Python TaskGroup | 4.600 秒 | 3 | 1 / 1 | 否，Fetch 本地拒绝 |
| Exa | 中国海油年报 | 5.387 秒 | 3 | 1 / 1 | 是，官方 PDF |
| Exa | Python TaskGroup | 5.591 秒 | 3 | 1 / 1 | 是，官方文档 |

六次运行都生成了最终回答，但只有 Tavily、Exa 的四次完成了要求的“搜索 + 正文核验”链路。**不能把“图正常结束”当成六题全部成功。** DeepSeek 的较短耗时包含快速安全拒绝，不能据此视为任务完成更快。

### 回答内容审查

- Tavily、Exa 的年报回答均给出 726.8 百万桶油当量、同比 7.2%，引用公司官方 PDF。返回给模型的正文包含所需数据，支持核心结论。
- 两家的 Python 回答均覆盖：其他任务取消、等待结束后聚合异常，以及 KeyboardInterrupt/SystemExit 特例。抽查所读正文可以支持核心结论。
- DeepSeek 年报回答给出同样数据，但明确说明两个页面均被拦截，只依据搜索摘要，没有声称读到了正文。
- DeepSeek Python 回答说明官方正文读取失败、镜像只能作间接线索，没有假装已完成官方正文核验。
- 仍存在表达质量问题：部分回答超出题设字数；正文有 20000 字符上限，模型却出现“无证据限制”等过强表述。后续验收应要求说明实际阅读范围，不应仅检查数值正确。

以上为单人对本轮轨迹的人工检查，未进行双盲评审或全面事实核验。

## 5. Token 与费用口径

| 工具提供方 | 两题合计模型输入 Token | 输出 Token | 输入中缓存读取 Token |
| --- | ---: | ---: | ---: |
| Tavily | 58442 | 539 | 29696 |
| DeepSeek | 47272 | 659 | 39168 |
| Exa | 56965 | 587 | 34048 |
| 合计 | 162679 | 1785 | 102912 |

模型调用合计 19 次；数据来自最终 AIMessage 的真实 usage_metadata 按调用求和。输入 Token 包含每轮重复发送的系统提示词、历史和工具结果，并非独立网页文本量。

工具调用本身：适配器层 30 次尝试中 5 次 DeepSeek Fetch 本地拒绝；Agent 层 6 次 Search、7 次 Fetch 尝试中，3 次 DeepSeek Fetch 本地拒绝。按脚本发起的调用统计，共 35 次进入供应商工具客户端；SDK 内部是否发生传输重试未单独抓包统计。适配器没有透传所有供应商用量字段，不能沿用上一轮直连账单当作本轮实付费用。

公平性限制：Tavily → DeepSeek → Exa 的运行顺序使后续模型请求可能受益于前缀缓存；各服务的内容长度和 SDK 网络路径也不同。没有多轮随机交叉，不能把当前任务耗时当成正式性能排名。

## 6. 验证与证据

- 首先编写并运行测试，复现可选标题 KeyError、配置串用、预算不可配置、空正文误报等问题及缺失的 DeepSeek 工具；修复后通过回归。
- 适配器、Tavily TLS、结果净化、输出预算，以及新增 Agent 调用预算的相关测试：**155 passed**。
- 修改文件的 Ruff 检查和格式检查通过；`git diff --check` 通过。
- 沙箱内 Windows 异步测试曾停滞；在正常本机环境运行同一相关集合后通过。全量后端回归另行尝试，结果见下方补记，不等同于相关集合通过。
- 首次集成驱动直接调用 ToolNode，缺少新版 LangGraph runtime，30 个驱动尝试均在执行工具前失败，没有发供应商请求。随后补充真实注册/净化离线回归，改为编译 StateGraph，再执行上表测试；首次驱动结果不计入服务统计。

复现入口：

- `scripts/benchmark_search_fetch_adapters.py`
- `scripts/benchmark_search_fetch_agent.py`
- `backend/tests/test_search_fetch_adapters.py`

原始记录（均在被 Git 忽略的目录）：

- `outputs/search-fetch-adapters-20261018-r2/`：逐次工具输出和汇总。
- `outputs/search-fetch-agent-20261018/`：六个任务的最终答案、模型/工具轨迹及 Token。
- `outputs/search-fetch-full-suite-native.log`：全量回归日志。

临时密钥文件再次使用后已删除；对本轮适配器与 Agent JSON 记录扫描全部三个精确密钥值，未发现泄漏。没有修改现有 .env 或 config.yaml，也没有提交或推送。

### 全量回归补记

完整后端测试在正常本机环境运行至约 13% 时达到预设 180 秒上限，期间已有失败，**全仓回归未通过**。随后以 `-x` 定位首个失败：

`tests/blocking_io/test_channel_runtime_config_store.py::test_configure_runtime_channel_does_not_block_event_loop`

报错为 `app/channels/runtime_config_store.py:63` 导入 `fcntl` 时发生 `ModuleNotFoundError`。这是未修改渠道模块依赖 Unix 文件锁、当前 Windows 不提供该模块的兼容性问题。首个失败定位运行结果为 1 failed、5 passed、3 skipped；不能据此解释全量运行中其余所有失败。没有为完成本轮测试而修改渠道模块或屏蔽这些测试。

日志：`outputs/search-fetch-full-first-failure.log`。相关 155 项仍全部通过，但这些结果不构成生产发布的全仓验收。

## 7. 下一步建议

1. 在实际 Gateway 网络、真实公网 DNS 下复测 DeepSeek Fetch，保持 URL 安全校验，不把本地拒绝混入上游统计。
2. 三家分别做随机顺序、多时段运行，增加中文政策与技术问题；测首试与重试后的成功率，单列缓存和新抓取。
3. 正文处理增加去导航、去重复和相关块选择，比较 4096 / 20000 字符预算对答案准确性和 Token 的影响。
4. 根据“来源命中 + 正文核验 + 引用支持”选 Search 与 Fetch，可混合厂商；目前不自动切换生产默认配置。
