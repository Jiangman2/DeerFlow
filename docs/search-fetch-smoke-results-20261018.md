# Tavily、DeepSeek、Exa：第一轮真实 Search / Fetch 对比

测试日期：2026-10-18，北京时间约 10:50。分支：`codex/search-fetch-comparison`。

## 结论

已实际调用每家 5 次 Search、5 次 Fetch，共 30 次，没有自动重试，没有模型调用，也没有切换生产配置。

本轮 DeepSeek Search 返回最快，但英文官方文档召回有明显缺口；Exa 的搜索结果更集中于目标原始资料。Tavily 与 Exa 均获取了测试 PDF 的正文，DeepSeek 对该 URL 返回 404。Exa Fetch 的默认缓存路径速度快，不能据此推断其实时抓取也同样快。

小样本下建议下一轮以 Exa auto 为搜索质量参照、Tavily basic 为现有能力基线，继续检验 DeepSeek 搜索的中文覆盖与低延迟价值。Fetch 优先比较 Exa 与 Tavily；若使用 DeepSeek，需要对 PDF 和较慢页面制定显式处理策略。这里不是最终生产选型。

## 1. 测试口径

这是官方 API 直连测试，**不是 DeerFlow 工具适配器或端到端 Agent 测试**。三个供应商各有独立连接，供应商内部串行请求；总并发为 3。客户端不读取环境代理、保持 TLS 验证、禁用重定向、不重试。连接超时 10 秒、读超时 35 秒，响应体限 2 MiB，并在流读取时检查总耗时。

Search：Tavily basic、DeepSeek need_content=false、Exa auto + highlights，每次请求 5 条。Fetch：Tavily basic Markdown、DeepSeek URL Fetch、Exa text(maxCharacters=20000)。所有正文另外统一截取最多 20000 字符作为模型侧预算，原始返回在本地保留。

**公平性限制：** Exa 的服务端文字上限为 20000，另两家没有同类请求字段，因此网络传输体积不同。Exa 自报政策页来源为 crawled，其余 4 页为 cached；另两家没有返回可直接对齐的缓存来源字段。不能把本表延迟解释成等载荷、全新抓取性能排名。

## 2. 总体数据

| 服务 | Search 非空结果 | Search 成功请求中位耗时 | Fetch 非空正文 | Fetch 成功请求中位耗时 |
| --- | ---: | ---: | ---: | ---: |
| Tavily | 4/5 | 2.040 秒 | 5/5 | 0.375 秒 |
| DeepSeek | 5/5 | 0.332 秒 | 4/5 | 6.172 秒 |
| Exa | 5/5 | 1.437 秒 | 5/5 | 0.283 秒 |

成功口径仅指 HTTP 200 且结果/正文非空，不等于内容准确或全文完整。此处中位耗时排除失败请求；原始 summary.json 的 median_s 则包含失败请求，另有 success_median_s 单列成功口径。5 个样本不足以报告有代表性的 p95 或可用性承诺。

Tavily 的 cn_energy 请求发生 ConnectionError，未收到 HTTP 状态，5.067 秒结束；不能判断服务端是否处理，更不能归因于密钥、余额或长期稳定性。其余 Tavily 调用成功。DeepSeek PDF 请求 HTTP 404，耗时 4.177 秒；这只能说明该供应商本次未获取到指定 URL，不能推出 DeepSeek 全面不支持 PDF，也不能推出目标文件不存在。

## 3. Search：逐项结果和人工观察

### 请求与耗时

| 样本 | 相同查询 | Tavily | DeepSeek | Exa |
| --- | --- | ---: | ---: | ---: |
| cn_energy | 中国海油 2024 年报 油气净产量 | 连接失败 / 5.067 秒 | 5 条 / 0.759 秒 | 5 条 / 2.049 秒 |
| cn_policy | 海上风电开发建设管理办法 国家能源局 | 5 条 / 2.648 秒 | 5 条 / 0.384 秒 | 5 条 / 1.437 秒 |
| en_research | offshore wind turbine predictive maintenance review | 5 条 / 1.789 秒 | 5 条 / 0.332 秒 | 5 条 / 1.403 秒 |
| en_documentation | Python asyncio TaskGroup cancellation documentation | 5 条 / 2.224 秒 | 5 条 / 0.307 秒 | 5 条 / 1.500 秒 |
| open_source | bytedance DeerFlow GitHub architecture | 5 条 / 1.855 秒 | 5 条 / 0.330 秒 | 5 条 / 1.161 秒 |

### 观察

- **中文年报：** DeepSeek 首条为公司官方年度业绩新闻，摘要包含查询目标净产量数值；Exa 首条为公司官方 2024 年报 PDF，同时命中巨潮年报和国资委报道。两者均相关，但资料形态不同。Tavily 本项没有结果可评价。
- **中文政策：** Exa 首条为国家能源局 2016 年管理办法，前四条均为能源局/地方发改部门页面。Tavily 首条同样命中能源局 2016 年办法。DeepSeek 首条为早期暂行办法，第二条命中自然资源部的 2016 年办法；展示时需要区分文件版本。本轮不据此判断法规现行有效性。
- **英文研究：** Exa 返回多篇综述的出版/DOI 页面；Tavily 返回综述、研究项目和企业技术页面；DeepSeek 混有文献索引、聚合检索页和相关文章。未下载全文或逐条核验 DOI，不计算论文召回率。
- **Python 官方文档：** Exa、Tavily 首条均为 docs.python.org 的 asyncio-task 文档。DeepSeek 的 5 条结果中没有 docs.python.org，包含第三方 PyPI 包、波斯语文档镜像、邮件、第三方仓库和源码镜像。这是本轮最明显的目标来源召回差异。
- **DeerFlow 架构：** Exa 首条为上游 main 分支 ARCHITECTURE.md，5 条均为上游仓库文件/页面。DeepSeek 首条也直接命中上游架构文件，但绑定历史提交，后续混有非上游资料；Tavily 首条命中上游仓库，后续包含讨论和外部文章。需注意历史文件与当前主线的区别。

这些是对返回标题、URL 和片段的人工检查，不是盲评，也不是标准化相关性评分。没有检查全部 70 条返回链接的可达性和事实准确性。

## 4. Fetch：相同 URL 的结果

| 固定页面 | Tavily：秒 / 返回字符数 | DeepSeek：秒 / 返回字符数 | Exa：秒 / 返回字符数 |
| --- | ---: | ---: | ---: |
| 国家能源局暂行办法 HTML | 2.209 / 9248 | 3.170 / 4493 | 1.568 / 4520 |
| 中国海油 2024 年报 PDF | 3.118 / 172424 | 4.177 / HTTP 404 | 0.361 / 20000 |
| Python asyncio-task 文档 | 0.334 / 60927 | 8.632 / 47231 | 0.246 / 20000 |
| Kubernetes Overview | 0.324 / 10697 | 3.713 / 9971 | 0.279 / 9471 |
| DeerFlow GitHub 首页 | 0.375 / 316780 | 11.084 / 324944 | 0.283 / 19995 |

字符数为客户端归一化截断之前收到的文本长度。Exa 的年报和 Python 文档已碰到请求上限，不能用较短结果推断正文获取能力差；反过来，字符更多也可能是导航、重复正文或额外页面信息。

抽查发现：

- 政策页三家都包含第一条与第三十八条。Tavily 包含表格化正文及后续重复，字符数约另两家两倍；DeepSeek、Exa 更紧凑。
- PDF：Tavily 返回长篇文本且含年报末尾备查文件目录；Exa 返回的前 20000 字符包含年报名称和净产量目标字段。未逐页核验表格和所有财务数据，不宣称完整准确。
- GitHub：Tavily、DeepSeek 返回超过 30 万字符，混有导航、目录等信息；简单取前 20000 字符会浪费内容预算。Exa 起始部分直接进入仓库介绍，但有服务端长度限制。
- DeepSeek 文本中存在较多 Markdown 转义；Tavily 有导航和链接噪声；Exa 多采用较紧凑正文。这是当前页面观察，需要后续量化。

固定 URL：

1. https://www.nea.gov.cn/2011-11/21/c_131260222.htm
2. https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf
3. https://docs.python.org/3/library/asyncio-task.html
4. https://kubernetes.io/docs/concepts/overview/
5. https://github.com/bytedance/deer-flow

## 5. 本轮用量

| 服务 | 从响应观察到的用量 | 金额口径 |
| --- | --- | --- |
| Tavily | Search 4 credits；Fetch 合计 1 credit | 合计 5 credits，按公开按量价 $0.008/credit 折算 $0.040；未核对账单或免费额度 |
| DeepSeek | 5 个 Search 成功、4 个 Fetch 成功；响应未返回计费字段 | 按公开 ¥5/千次估算 ¥0.045；不是已核对账单 |
| Exa | Search 返回 costDollars 合计 $0.035；Fetch 合计 $0.005 | API 自报成本合计 $0.040；未核对账户账单或抵扣 |

Tavily 单页 Extract 的响应 credits 依次为 1、0、0、0、0，说明不宜把每个独立调用机械按 1 credit 计费。本轮仅保留响应观察，不推断其后续记账周期。连接失败的请求计费状态未知。没有模型 Token 成本。

单价参考：[Tavily](https://docs.tavily.com/documentation/api-credits)、[DeepSeek](https://api-docs.deepseek.com/zh-cn/guides/search/)、[Exa](https://exa.ai/docs/admin/pricing)。

## 6. 对接入方案的影响

1. **Search 和 Fetch 独立配置。** DeepSeek 搜索速度在本轮突出，正文提取则更慢，不能按同一家绑定选择。
2. **先解决正文预算。** 现有适配器前 4096 字符截取过短；统一预算后，还需要去导航、去重复或按问题选块，不能只提高字数上限。
3. **保留版本与来源。** 政策旧版、历史 Git 提交、语言镜像都可能相关，但不等价于当前官方文档。
4. **失败显式分类。** 网络连接失败与目标 Fetch 404 分开呈现；初期不要静默切换厂商，以免掩盖成本和覆盖差异。
5. **下一轮再比较完整 Agent。** 固定模型和上下文预算，将缓存/新抓取、HTML/PDF、中文/英文分层，多时段重复；本轮不足以替代这些验证。

## 7. 证据与复现

- 可复用脚本：`scripts/benchmark_search_fetch.py`。
- 原始结果：`outputs/search-fetch-comparison-20261018-smoke/`，每次请求一个 JSON，另有 summary.json；该目录被 Git 忽略，不提交第三方正文。
- 临时密钥文件已删除；输出文件经过精确密钥值扫描，未发现密钥。报告与脚本不含密钥；没有改动现有 .env。
- 本地验证：脚本语法编译、CLI 帮助、离线重算 30 条记录和成功中位耗时、结果文件密钥扫描。没有运行无关后端测试。
- 再次执行脚本会产生新的付费调用，必须使用新的输出目录。可通过三个 API Key 环境变量注入；请勿把密钥放在命令行参数中。

鉴于密钥曾通过聊天传递，测试结束后建议由账户持有人撤销或轮换；本地临时文件删除不等于对话记录中的密钥已消失。
