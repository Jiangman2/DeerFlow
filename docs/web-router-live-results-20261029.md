# DeepSeek 优先 Web 路由：本机 Docker 实际验收

日期：2026-10-29（Asia/Hong_Kong）。测试源码：`aa9741fc3`，对应 `codex/search-fetch-comparison` 的路由实现。验收脚本为本轮新增的 `scripts/benchmark_web_router_live.py`，未修改路由器源码。

结论：**首轮 8 项中 6 项符合预期；更新失效的 DeepSeek Key 后补验 2 项通过，8 个预定场景最终全部有真实通过证据。** PDF 自动路由 Tavily、Tavily 失败后转 Exa，以及 Search / HTML Fetch 的两级回退均已验证。不能写成“单轮 8/8”：首轮凭据问题、原始记录和补验记录均保留。

## 环境与证据范围

远程测试环境在本机不可连接，用户同意改用本机测试并启动 Docker。当前主工作区在另一个安全修复分支，因此本轮使用独立工作区，不切换或修改该分支：

`C:\Users\zc752\.codex\worktrees\web-router-live-validation\DeerFlow`

运行真实 DeerFlow 工具注册、AppConfig、异步 LangGraph ToolNode 和结果净化中间件，通过真实公网 API 进行 Search / Fetch。图中提供真实 HumanMessage 和工具调用；没有模型请求、供应商响应 mock 或端点替换。

这是本机容器内的工具集成验收，不是远程测试站点、Gateway HTTP、浏览器、登录、模型自主选工具或 Kubernetes 发布验收；不代表当前生产源码锁或部署镜像已通过验收。

基础镜像 `deer-flow-backend:ca-fix-20261026` 缺少本分支导入链所需的 SQLAlchemy，且其框架版本与源码不兼容。两次预检均在调用供应商前失败，未产生工具 API 请求。随后单独构建 `deerflow-web-router-test:20261029`，没有覆盖原镜像标签或修改运行中的服务：

- 基础镜像 ID：`sha256:2efffe341e1995dd6957e34dc4b42a9d8dc28eee7b40867737cf1ade20d1669e`。
- 验收镜像 ID：`sha256:afd5f21590dff81eef45f6477554208949245fded796b8e427f9b51086314312`。
- Python 3.12.15；LangChain 1.2.15、langchain-core 1.3.3、langchain-openai 1.2.1、LangGraph 1.1.9、langgraph-prebuilt 1.0.11、OpenAI SDK 2.32.0，与本机此前已验证的框架版本对齐。
- 测试镜像另安装 SQLAlchemy 2.0.49、asyncpg 0.31.0、psycopg[binary] 3.3.3 及必要依赖，仅满足源码导入链，不连接业务数据库。
- 源码只读挂载、凭据文件只读挂载，输出单独挂载。验收容器均使用 `--rm`，完成后已退出并自动删除；验收镜像保留以便复现。

## 用例与最终结果

Search 固定查询：`中国海油 2024 年报 油气净产量`。

PDF：`https://ltd.cnooc.com.cn/tzzgx/dqbg/nb/202503/P020250327605146009602.pdf`

HTML：`https://ltd.cnooc.com.cn/xwzx/gsxw/2025/202503/t20250327_114400.html`

每项独立构造 AppConfig，默认 DeepSeek → Tavily → Exa，PDF 为 Tavily → Exa；结果数 5、片段预算 1000、正文预算 20000 字符，单次超时 15 秒、总预算 40 秒、最多 3 次尝试。失败注入只在该用例的内存配置里替换指定供应商的凭据，远端返回真实 HTTP 401；不是缺失 Key 跳过，也不是模拟 HTTP 响应。

耗时包含用例内图构建与工具调度，不含容器冷启动及模块导入，不能当作纯供应商请求耗时。

| 场景 | 实际顺序 | 最终结果 | 用例耗时 | 输出 |
| --- | --- | --- | ---: | --- |
| DeepSeek 正常搜索（补验） | DeepSeek 成功 | 通过 | 1.780 秒 | 5 条、2500 字符 |
| DeepSeek 正常 HTML（补验） | DeepSeek 成功 | 通过 | 5.988 秒 | 2717 字符 |
| PDF 自动路由 | Tavily 成功，未调用 DeepSeek | 通过 | 2.187 秒 | 20079 字符 |
| Search 回退 Tavily | DeepSeek 401 → Tavily 成功 | 通过 | 1.285 秒 | 5 条、5170 字符 |
| Search 回退 Exa | DeepSeek 401 → Tavily 401 → Exa 成功 | 通过 | 4.228 秒 | 5 条、3907 字符 |
| PDF 回退 Exa | Tavily 401 → Exa 成功，未调用 DeepSeek | 通过 | 2.708 秒 | 20047 字符 |
| HTML 回退 Tavily | DeepSeek 401 → Tavily 成功 | 通过 | 2.903 秒 | 5130 字符 |
| HTML 回退 Exa | DeepSeek 401 → Tavily 401 → Exa 成功 | 通过 | 2.901 秒 | 1999 字符 |

PDF 返回字符数包括标题、截断说明及供应商标记，因此可以略超 20000 字符正文预算。两家 PDF 正文和三个供应商的 HTML 正文均含净产量 726.8、同比 7.2 的数字；这只是关键值检查，不代表全文完整或答案质量评分。

## 首轮凭据问题与补验

首轮未注入故障的两个 DeepSeek 用例都收到 API 401，自动由 Tavily 返回结果。因此它们没有通过“DeepSeek 直接成功”的预期，不算成主服务通过，也没有被覆盖为后来的成功结果。回退机制在这两次非预期认证失败中仍正常发挥作用。

收到用户更新的 DeepSeek 测试凭据后，只补跑 `search_primary`、`html_primary` 两项，不重复此前已经通过的六项。两次均只调用 DeepSeek。没有为迁就结果修改路由规则、错误分类或安全校验。

- 首轮：8 次工具执行、17 次供应商请求，6 项预期通过，2 项主服务未验收。
- 补验：2 次工具执行、2 次供应商请求，2 项通过。
- 合计：10 次工具执行、19 次供应商请求；每个尝试都在 `artifact.web_routing.attempts` 有记录。没有同供应商重试、模型请求或源站预抓取。
- 这是请求轨迹统计，未核对三家账户实际扣费，不能作为账单结果；本轮亦未做统计性能排名。

## 证据、源码身份与清理

证据位于本工作区 `outputs/web-router-local-20261029/`，Git 忽略；原始副本亦保留在 `D:\zc752\Documents\Github\DeerFlow\outputs\web-router-local-20261029\`。

- `results/`：首轮 8 个案例、首轮汇总。
- `primary-retest/`：2 个补验案例、补验汇总。
- `combined-summary.json`：按场景合并的最终结论，另保留两轮计数。
- `runtime-receipt.json`：实际镜像及框架版本；`Dockerfile`：隔离测试镜像的构建步骤。
- 两轮收据中的 `web_router/tools.py` SHA-256 均为 `edc3b079821e54eb1f2ea9940ef0c15e8bb54cc3af807ea688964971250f7ffb`，`providers.py` 均为 `ce25eda22bac716dfa150680a9e83df56d971e114d91bd9e26fab8e2478e7319`，验证期间未修改生产路由实现。

本轮结束前扫描输出及脚本共 16 个文件，未发现三个当前 Key 或此前失效 DeepSeek Key 的精确值；临时凭据文件已删除。对外不发布密钥、原始认证头或配置对象。没有改动原工作区已跟踪源码、生产配置、源锁、服务、数据库或旧 stash，没有提交、推送或部署。

可复用脚本支持 `--check-config`（只构建真实工具图，不发请求）和 `--cases`（仅补验指定场景）。例如在上述容器挂载约定下：

```sh
/app/backend/.venv/bin/python /test/src/scripts/benchmark_web_router_live.py \
  --key-file /run/keys.json \
  --output /test/output/new-run \
  --source-revision aa9741fc3 \
  --cases search_primary html_primary
```

`--output` 必须是新目录，防止覆盖历史证据。复现时重新提供运行时凭据，不能依赖已删除的文件。本轮未新增业务功能，不重复声明或改变此前 294 项离线回归的历史结果。
