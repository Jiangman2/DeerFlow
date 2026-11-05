# DeepSeek Fetch：PDF 失败边界诊断

测试时间：2026-10-19 10:38 起（Asia/Hong_Kong）。分支：`codex/search-fetch-comparison`。

结论：本轮 **4 个 PDF URL 全部被 DeepSeek Fetch API 返回 HTTP 404，3 个 HTML URL 全部成功**。源站对这 7 个 URL 均直接返回 HTTP 200。两个官方域名的年报 PDF 字节完全相同，Tavily、Exa 均能读取。因此证据更支持“当前 DeepSeek 云端 Fetch 对本组 PDF 的获取或解析存在能力边界”，而非原 URL 失效或整个官方域名不可访问。API 没有提供失败阶段，仍不能断言所有 PDF 都不支持，或将内部原因确定为解析器、反爬、缓存、网络中的某一项。

2026-10-19 补充：用户在官方 DeepSeek Harness 中使用同一 PDF / HTML URL 复测，云端 Fetch API 同样为 PDF 404、HTML 成功。该失败可以跨宿主复现，并非 DeerFlow 适配器独有问题。用户提供的结果与证据范围见下节。

## 方法

新增可复用脚本 `scripts/diagnose_deepseek_pdf_fetch.py`。沿用真实 AppConfig、工具注册、LangGraph ToolNode、结果净化及原适配器，通过公网官方 API 执行；没有修改生产适配器、错误语义或默认工具。

- 7 次 DeepSeek Fetch：2 个年报 PDF 域名、2 个业绩 HTML 域名、W3C 测试 PDF、arXiv 论文 PDF、arXiv 摘要 HTML。
- 4 次对照 Fetch：Tavily、Exa 各读取两个年报 PDF URL。正文预算均为 20000 字符，DeepSeek 超时配置仍为 30 秒。
- 7 次本机源站 GET：不带任何 API 凭据、`trust_env=false`、禁用重定向和自动重试。记录 HTTP 状态、类型、大小、PDF 文件头及完整响应 SHA-256；每次最多 24 MiB，读超时 20 秒、连接超时 8 秒，另有流读取累计时间检查。源站探测只使用脚本固定公开 URL。
- 没有模型调用、搜索调用或自动跨服务回退。对照请求是预先设计的独立诊断案例。没有核对 SDK 内部传输重试或实际扣费。

## 实测矩阵

| 样本 | 本机源站 | 源站正文大小 | DeepSeek Fetch | 耗时 |
| --- | --- | ---: | --- | ---: |
| 中国海油年报 PDF，ltd.cnooc.com.cn | 200，application/pdf | 1485912 字节 | API 404 | 5.983 秒 |
| 同路径年报 PDF，www.cnoocltd.com | 200，application/pdf | 1485912 字节 | API 404 | 6.775 秒 |
| 年度业绩 HTML，ltd.cnooc.com.cn | 200，text/html | 22467 字节 | 成功，2775 字符 | 5.319 秒 |
| 同路径业绩 HTML，www.cnoocltd.com | 200，text/html | 22467 字节 | 成功，2775 字符 | 7.795 秒 |
| W3C dummy.pdf | 200，application/pdf | 13264 字节 | API 404 | 0.220 秒 |
| arXiv 1706.03762 PDF | 200，application/pdf | 2215244 字节 | API 404 | 0.254 秒 |
| arXiv 1706.03762 摘要 HTML | 200，text/html | 43644 字节 | 成功，3326 字符 | 0.308 秒 |

源站 4 个 PDF 响应均有 `%PDF-` 文件头，全部响应完整下载，无重定向。PDF 共 4 个 URL、3 个不同文件；HTML 共 3 个 URL、2 个不同页面，不能当成 7 个独立站点样本。

两个年报 PDF 的 SHA-256 相同：

`802b2139219c681fc2d7a3c9360ca9ff36defe4f54dee9cf5e67336f42ed48ce`

两个业绩 HTML 的 SHA-256 也相同：

`04abd4ba45b1340267a968d0bbb0ebcfa1cc86ea7dae0ef48634fe1fee029c50`

| 对照服务 | ltd.cnooc.com.cn 年报 PDF | www.cnoocltd.com 年报 PDF |
| --- | --- | --- |
| Tavily | 成功，3.273 秒，20053 字符 | 成功，2.442 秒，20053 字符 |
| Exa | 成功，0.889 秒，20024 字符 | 成功，0.562 秒，20024 字符 |

两家返回内容均含 726.8 与 7.2，年报文本与此前核验事实相符；成功只代表读取到非空正文，不代表返回完整 244 页。

DeepSeek 两个业绩 HTML 返回中数字是 Markdown 转义形式 `726\.8`、`7\.2`。诊断脚本最初的字面匹配未识别转义，运行后修正为消除小数点转义的词法标记，并离线重算 `contains_energy_values`。没有改变原始正文、HTTP 结果、耗时，也没有因此重发付费请求。该标记只表示文本含数字，不是事实质量评分。

## 错误归因与边界

四次失败均由现有适配器收到供应商 API HTTP 404，输出：

```json
{
  "error_code": "DEEPSEEK_PROVIDER_NOT_FOUND",
  "retryable": false,
  "error_scope": "provider_api",
  "http_status": 404
}
```

这不是本地 URL/DNS 校验拦截，也不是 Markdown 格式化失败。`http_status` 始终指 API 响应状态，不是目标站状态。本机源站为 200 也不能证明 DeepSeek 服务端访问时一定得到相同响应。

目前可区分：

1. **具体 URL 失效：不获本轮证据支持。** 原 URL、另一个官方域名均可直读且字节相同，两个对照服务也成功。
2. **整个官方域名不可访问：不获本轮证据支持。** DeepSeek 在同一两个域名的 HTML 均成功。
3. **只与这份大型中文年报有关：不获本轮证据支持。** 13 KiB 的 W3C 简单 PDF 和另一域名的英文论文也失败；不是只有 244 页中文年报触发该现象。
4. **本组 PDF 存在服务能力或格式相关限制：较符合现象。** 但格式、站点具体路径、缓存及服务端抓取政策尚未完全隔离；没有受控同源、同内容、不同 MIME/扩展名实验，不能宣称已确定内部根因。

官方 [Fetch API 文档](https://api-docs.deepseek.com/api/fetch/) 将接口描述为网页转 Markdown，只公开 `url` 请求与 `title`/`content` 响应，未在该页面承诺 PDF 提取。官方 Harness 的 [HTTP Fetch Provider 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/web/web-fetch-http/README.md) 另有二进制/PDF 限制描述，但它属于独立本地 HTTP Provider，不能拿它证明云端 Fetch API 的内部实现相同。

## 官方 Harness 复测：用户提供的独立记录

记录接收日期：2026-10-19。证据来自本次对话中用户粘贴的工具结果及操作说明，不是本仓脚本直接采集；未提供 Harness 版本、完整配置、原始会话日志或耗时。以下结果不并入上文 11 次适配器调用的成功率、耗时或费用统计。

测试 URL 与本仓诊断相同：

- PDF：`https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf`
- HTML：`https://ltd.cnooc.com.cn/xwzx/gsxw/2025/202503/t20250327_114400.html`

### 第一轮：DNS 安全校验拦截，不计入云端 API 对照

PDF、HTML 均返回：

```text
Error: URL hostname "ltd.cnooc.com.cn" resolves to a non-public IP address
```

该错误与 Harness 本地 `http` Fetch Provider 的 DNS 安全策略一致，但未凭此条文本独立核验有效 provider 配置。两次均未得到目标 HTTP 响应或正文，无法判断 PDF 解析能力，不能与云端 API 404 混为同一失败类型。

用户粘贴的模型解释将该站点推断为“内部站点”，超出了错误可证明的范围。错误只说明该运行环境解析到的地址被安全策略判为非公网；Fake-IP、企业 DNS 等只是可能原因，未经诊断。此前本仓源站探测已直接得到 HTTP 200，不能据该错误判定站点仅内网可达。

### 第二轮：DeepSeek 云端 Fetch API

用户重新测试后，PDF 的工具结果明确标出实际端点 `https://api.deepseek.com/v1/tools/fetch`，原始错误为：

```text
Error: DeepSeek API error (HTTP 404): failed to fetch URL
(request_id: 6173e34c-1cae-4c02-9d96-301de65c155c)
```

| 测试对象 | 用户提供的 Harness 结果 | 本仓 DeerFlow 结果 | 对照判断 |
| --- | --- | --- | --- |
| 相同年报 PDF URL | API HTTP 404，failed to fetch URL | API HTTP 404，DEEPSEEK_PROVIDER_NOT_FOUND | 失败现象一致 |
| 相同业绩 HTML URL | 成功，工具展示 HTTP 200，有正文 | 成功，返回 2775 字符 | 读取成功一致 |

用户说明两项各仅调用一次 `web_fetch`，未搜索、替换域名、下载后本地解析或使用其他工具。HTML 返回包含业绩公告开头，并被报告为从标题、日期至编者注、联络方式及页脚均可见，无截断标记。未独立取得完整正文或逐字核对源页面，因此记录为“用户报告未截断”，不额外认证为源网页完整副本。Harness 展示的 HTTP 200 也不单独视为目标服务器状态抓包证据。

复测支持以下结论：

- 相同官方云端端点、相同 URL 的 PDF 失败已在官方 Harness 中复现，不能归为 DeerFlow 独有的适配器缺陷。
- 本地 DNS 拦截与云端 API 抓取失败是不同阶段，必须分别记录；这次云端对照不要求关闭或放宽本地 DNS 安全检查。
- API 404 仍不是源站 404，且未暴露获取/解析的内部失败阶段；没有新增“全部 PDF 均不支持”的结论。
- 上述 request_id 可供后续向 DeepSeek 服务方反馈定位；本次没有发送反馈或发起新请求。

## 证据、验证与后续含义

- 完整脱敏记录：`outputs/deepseek-pdf-diagnostic-20261019/`，7 个源站记录、11 个适配器记录、1 个汇总。
- 新脚本 Ruff 检查、格式检查及 `git diff --check` 均通过。本次没有变更生产实现，也没有重跑全仓回归。
- 逐文件扫描 19 个输出文件及脚本，未发现三个精确 API 密钥值；临时密钥文件已删除。
- 浏览器检索亦可读取 [W3C PDF](https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf)、[arXiv 论文](https://arxiv.org/abs/1706.03762)、[年报原 URL](https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf) 与[另一官方域名](https://www.cnoocltd.com/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf)，仅作辅助核验，矩阵数值来自本轮脚本。

本轮足以把下一步实现目标定为“PDF Fetch 的明确能力提示与可选通用回退”，无需通过放宽本地校验来处理此错误。尚未实现该回退、提交、推送或部署。
