# Data QA 新接口接入与联合发布

日期：2026-10-21。状态：新 App 单次现场接口调用已完成；尚未创建线上配置或发布。

## 变更边界

当前企业环境的问数工具最终为 `datalake_smart_qa` 和 `datalake_data_qa`。
`datalake_data_qa` 独立调用新接口，并非把旧明细、指标 API 拼接或合并调用：

- POST `http://10.77.124.142:9080/appforge/openapi/v1/InvokeApp/app-v16p2syh`
- 专属 Secret 变量：`APPFORGE_DATA_QA_API_KEY`；不回退公共或旧工具密钥。
- `inputs` 固定为 `{"hytoken":"","hyaccount":""}`，不继承旧身份配置。
- 会话前缀 `SquareWorkflow_`，按可信用户、thread 和工具隔离，未提供 thread 时随机。
- 模型参数只有必填 `query`，实际问题去除首尾空白后传给新接口。

Smart QA 保留 Retrieve 地址、项目、文档库及图片代理能力。仓库可选的
`datalake_md_search` 不在本次企业配置中启用。旧工具名称和旧 App ID 不再用于
远程调用；历史消息保持原样，未执行的旧工具调用收到本地迁移提示。

## 配置迁移

`deploy/enterprise/data-qa-config-migration.py` 是纯数据变换，不写文件、不操作集群。
它复制现有配置，仅替换旧工具注册；Smart、模型、Sandbox 和其他配置保持原值。
它拒绝重复注册、意外主数据工具、旧 provider 别名，以及自定义 Agent 白名单、
拒绝名单或提示词中待人工审阅的旧引用，避免静默扩大权限或遗漏迁移。

发布控制器应创建新版本 ConfigMap 和专属 Secret，通过候选镜像加载配置后，
在同一次 Deployment 模板更新中切换镜像、ConfigMap 与 Secret 引用。
不得先修改正在被旧 Pod 使用的 ConfigMap。回退同样需要恢复成套的旧镜像、
配置与凭据引用，不删除旧材料。本次不需要数据库迁移或前端工具按钮变更。

用户要求本次与已验证的 GLM `TERMINAL_AND_DONE` 修复合并发布；此前单独 GLM
切换工具暂不执行。候选镜像仍需依据现场源码摘要组装，不能直接覆盖整个开发目录。

## 流与错误处理

新工具仍通过 `httpx.Client.stream` 增量读取 SSE。新增的有界解析器处理 SSE
分帧、心跳、多行 data、业务终止、澄清、错误及答案增量/最终全文去重。
HTTP 200 下失败、坏帧或没有业务结束的断流不能包装为成功；不自动重放工作流。
保留原 JSON 字段及 5,000 字答案、10,000 字总输出预算，不返回原始内部思考。
180 秒是 HTTP 超时配置，不是严格的整体耗时上限。

初始离线格式来自现有 AppForge 适配器。现场单次调用现已确认 `event=answer`
和 `workflow_finished`、`data.status=succeeded`、`data.outputs.output` 及
`xx_ceai_extra_data` 用量结构。回归测试复现已观察的结构，使用合成答案替换业务文字；
被脱敏的事件名、节点类型仍标记未知。错误、空结果和澄清仍缺真实样本。
离线成功不等于业务数据准确或上游权限隔离已验收。

## 数据湖来源限制

明确的数据湖/企业表查询在未授权时，直接提示“请先点击数据湖按钮开启权限”，
不调用外部搜索。模型在所有运行中收到相同的来源选择规则；表达不明确时应澄清。
主机预判使用明确的数据湖来源及企业表查询措辞，不把通用数据湖概念说明当成问数，
也不声称关键字规则能够识别所有自然语言问题。

本轮一旦尝试 Smart/Data QA（包含旧工具迁移调用），就保存来源限制，工具返回空、
失败、未授权或成功均不解除。模型工具列表和工具执行边界都限制外部检索、浏览器、
未知 MCP 及任意脚本执行；允许数据湖查询、澄清和受控本地文件处理。并行工具批次
不能抢在数据湖结果前访问外部来源。来源限制随 task 传给子智能体，并在同次运行
的历史压缩后保留。独立的新公开知识问题不继承上一轮限制。

这一保护控制数据来源，不会授予数据湖权限，也不以网络公开资料替代缺失的企业数据。

## 现场待验证

1. 原工具、GLM 及新增来源限制涉及的 task/子智能体源码均已核对；Gateway 仍为 generation 381。
2. 已通过服务器隐藏输入完成新接口专属密钥的单次验证；工具未保存或创建 Secret。
3. 业务方原题“查询作业井基本信息表中设计井深最大的2口井”已返回 455 字答案，
   正常结束且未截断。脱敏回执不含答案，排序、字段、条数及值仍待业务验收。
4. 业务方认可的指标/汇总问题目前暂缺，指标能力保留为未验收。
5. 验证追问、澄清、空结果和授权关闭后的拒绝；会话 ID 隔离不能代替上游权限证明。
6. 再核对 Smart QA 原接口及用户可见功能，形成联合候选镜像和切换/回退工具。

现场读基线工具：`deploy/enterprise/inspect-data-qa-baseline.py`；只查询一个稳定
Gateway 样本，不读取或打印密钥，不调用模型或新接口，不修改工作负载。

## 已收到的现场回执

- 操作：`18cdf0ea7de24ecabd963a6f3abce69f`，仅 1 次新 App 请求。
- 报告：`/var/lib/portal-data-qa-canary-18cdf0ea7de24ecabd963a6f3abce69f.json`，3,983 字节。
- SHA-256：`e8000fc35a03bd1434e99d9324530bd5cd1de302e1cc42b5c3f07b2c119f2987`。
- Gateway generation 381，spec SHA-256 `5a84f74566171e588433779a59b9adec1ae752c2dc3c42706ff55db1e295fbda`。
- `result_status=done`、`answer_present=true`、`truncated=false`、`error_code=null`。
- 没有创建 Secret、修改配置或工作负载；`business_values_verified=false`、`ready_for_rollout=false`。
- 本地相关测试此前 283 通过、1 跳过，独立联调控制器 6 项通过。全量后端测试未完成：
  中断时 8,162 通过、84 跳过、50 失败；失败包含 QA 镜像缺少 Flask、子进程加载旧
  harness 路径、不可访问的宿主 Git worktree 元数据及未设置 PWD。
  修正隔离测试的 harness 挂载和 PWD 后，Gateway 导入及凭据环境相关 111 项通过。
  没有修改生产代码来掩盖这些环境问题；全量回归仍未通过验收。

## 联合候选包（尚未切换）

最终相关回归（包括新增脱敏结构、来源限制、委派、GLM 流完整性、Gateway 导入和
harness 边界）为 **320 通过、1 跳过**。Ruff check 通过。完整格式检查仍有既存
`test_enterprise_glm_wire.py` 的格式差异，未在本次修改该文件。

`package-data-qa-glm-preload.py` 基于原 66 层 ARM64 Gateway 追加一个固定源码层，
包含 11 个 Data QA/来源限制文件及 4 个已经现场验证的 GLM 文件，不携带配置或密钥。
Provider 验证过的 AppForge 工具和 SSE 解析器与验证脚本中的源码逐字节一致；来源规则
另增加了“有哪些/是多少/最大”等明确查询表达的离线覆盖，不声称其已在线发布。

- manifest：`sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5`
- config：`sha256:f06bcc06c59e7b4cc342e2402b34a835fe98f826af2bc8b4e4132f40e1249468`
- 预载脚本：`preload-portal-gateway-data-qa-glm-20261021-r1.py`，289,018 字节。
- 脚本 SHA-256：`4213b68bdf9b67a6c1c92c4adc24250121dcbb2910c7abed40ba3ab12d4477e1`。
- Linux Python 3.7 的元数据、文件集合及篡改拒绝检查共 6 项通过。

此包仅预载，不改变运行中 Gateway。五台联合镜像预载均通过，实际回执已收录于
`deploy/enterprise/data-qa-glm-preloads-20261021-r1.json`，旧 GLM 单独预载
不能代替。配套配置迁移、Secret 引用及候选运行时验证已准备，现场尚未执行；不得
执行旧的单镜像 GLM 切换脚本。现有 Smart、模型和非工具配置必须在迁移中保持原值，
存量自定义 Agent/持久化设置中的旧工具引用仍需在实际迁移前检查。

`data-qa-glm-deployment-plan.py` 已实现纯计划函数：要求绑定当前 UID、generation、
spec 摘要及不可变资源回执，生成带 resourceVersion/spec 比较条件的三处变更。
回退函数拒绝覆盖切换后的额外修改，成套恢复保存的旧 spec；函数本身不提交补丁。
配置迁移与发布计划合计 18 项离线测试通过。完整执行控制器仍须绑定五台实际预载
回执、资源创建/读回和候选运行时验收后才能交付执行。

## 配置准备与候选检查

`package-data-qa-glm-preparation.py` 绑定上述五台预载和两份现场接口报告，生成
`prepare-portal-data-qa-glm-20261021-r1.py`。在 datax-01 以隔离 Python 执行
`--prepare-data-qa-glm-once`，隐藏输入与成功联调相同的新接口专属密钥。旧联调未保存
密钥或其指纹，因此本阶段不能独立证明输入与旧联调相同；不会再次调用业务接口。

工具只创建独立不可变 ConfigMap、专属 Secret，以及两个顺序运行的候选 Job。
它不修改 Deployment、原 ConfigMap、默认模型或正在运行的 Gateway。Job 不使用
`app=gateway` 标签、生产卷或服务账号令牌；每个 Job 限时 180 秒、禁止自动重试，
结束 600 秒后交给 Kubernetes TTL 清理，报告不提前声称已完成清理。
候选进程禁止 socket 连接，检查实际镜像中 15 个源码摘要、新配置/密钥引用、
两个 DataLake 工具、关闭权限提示、外部兜底拒绝、委派限制及 GLM terminal+[DONE]。
ConfigMap 转换保留非旧工具配置，原配置与新配置采用不同名称以便成套回退。

资源创建前保存可能已生效的操作意图，超时或未知结果不自动重复。重复运行需要先
检查报告；不删除 once 标记或放宽基线。40 项准备/迁移/计划离线检查通过，候选
15 源码文件在本地隔离 QA 运行时的合成配置检查通过；这不是 ARM64 现场验收。
尚待实际候选 Job 回执、持久化自定义 Agent 的旧引用核对及联合切换控制器。
新接口业务数值、排序和统计问题仍未验收，预载或配置检查不代表业务验收通过。

交付脚本 55,390 字节，SHA-256 为
`e4e5b461f79737a4eb15a32e046ce6db92b617c63364efa127148b64ce023860`。
完整包与内嵌代码已通过 Linux Python 3.7 编译/导入和预载绑定检查。

### 首次配置准备现场结果（未切换）

操作 `2e0fd403b23a4cf5b880daaee1b8e504` 已创建并读回不可变 ConfigMap
`deer-flow-config-data-qa-glm-2e0fd403b23a`（UID
`5aefbbbc-fee1-4745-99f9-54d092b6ad4d`）和专属 Secret
`deer-flow-data-qa-2e0fd403b23a`（UID
`b2f728c8-8129-4d4f-98ba-48ce9b730f1f`）。配置数据摘要为
`046185f02faccb041f6ce0f853d9847c84d6f01674e812210662aed4da57c9f6`。
未调用 Provider、未更新 Deployment，候选运行检查尚未通过。

首个 Job `portal-data-qa-check-2e0fd403b23a-0` 等待约 180 秒后，原工具把空或
异常 Pod 集合统称为 `candidate_pod_owner`；这不能证明镜像代码失败，也不能证明
具体的集群准入/配额原因。收到的报告 6,520 字节、SHA-256
`5e389a9a1710b239159e3d4872329943db55add3fcc52f66af7ad41c55780c98`，
实际回执保存在 `data-qa-glm-preparation-failure-20261021-r1.json`。

源码已将缺失 Pod、数量异常和归属异常分开报告；终态检查同时识别 Job 条件，并在
拒绝前保存脱敏事件类别/资源字段。事件读取失败不能覆盖原失败。该修正未覆盖已交付
r1 脚本，也未重新创建任何线上资源。

随后现场事件确认 Pod 创建后因 `untolerated taint` 无法调度，180 秒后
`DeadlineExceeded`。目标节点带 `dedicated=gateway:NoSchedule`，正式 Gateway
已有对应 Equal toleration，但原临时 Job 漏了该设置；候选代码尚未执行。

`package-data-qa-glm-resumption.py` 生成独立 r2 续接工具，校验原报告字节/摘要、
已有 ConfigMap/Secret UID、不可变性及配置摘要，保留原 once 标记。只创建两个新
检查 Job，复制正式 Gateway 已有的精确 toleration，并保留预载节点限制、180 秒
期限和零重试。无需重新输入密钥，不创建配置或 Secret，不修改 Deployment。
新增启动状态快照会保留 Pod 消失前的阶段信息，不导出原始日志或凭据。
准备、续接、迁移及发布计划相关 62 项离线检查通过；仍需现场续接回执。

交付 `resume-portal-data-qa-glm-candidate-20261021-r2.py`，78,554 字节，
SHA-256 `14c2108edf0d777f1dd2092624b6912caff40cda307a9705ad2a5855d49f094c`。
完整包、内嵌迁移/候选/复用代码通过 Linux Python 3.7 编译、加载与预载绑定检查。
复制至 datax-01 `/home/cnooc/` 后执行：

```bash
sudo python3 -I -B -u /home/cnooc/resume-portal-data-qa-glm-candidate-20261021-r2.py --resume-existing-candidate-once
```

原报告、已创建资源和原 once 标记须保留。续接失败时保留新报告，不自动重跑。

### 候选检查通过与联合切换工具

续接 `84193cd46073486cb9b2451d6cc61010` 的两个 Job 在 0018/0019 成功完成，
15 个文件、两个数据湖工具、新密钥引用、关闭权限提示、7 类外部兜底限制、空结果/
错误/委派范围和 GLM terminal+[DONE] 均通过。报告 8,677 字节，SHA-256
`b96301c50c6d04e66ac1a600596039362df2f20336bb459fcd22e6788cd8ed2a`。
Gateway 仍为 generation 381，未修改；启动观察中的 Pending 是先前快照，最终
Job Complete 和检查结果均为成功。TTL 清理尚未单独确认。

`package-data-qa-glm-activation.py` 从摘要固定的既有 GLM 切换控制器构建联合版本，
绑定这份回执、旧失败报告、已有不可变资源、两份接口联调回执和五台预载。启动先
只读检查存量个人/团队/旧版 Agent 配置及 SOUL.md 的旧工具引用；有引用、扫描
不完整或两节点视图不一致均在补丁前停止，不自动改写用户文件。

确认任务结束后，通过 `--activate-gateway-data-qa-glm-once --tasks-drained`
执行。仅一次 JSON Patch 同时改镜像、ConfigMap 名和专属 Secret 环境引用，
带 UID/resourceVersion/generation/完整 spec 比较条件。旧配置和 Secret 保留；
原始 spec 与期望 spec 保存到 root 私有目录，回退必须成套恢复，不能只退镜像。
这不是自动回退工具；超时或不确定写入不重复提交。前端/PPT runner/Provisioner/
Nginx spec 和 Gateway Service 均核对保持不变。

滚动完成后逐个核验 70 个副本的镜像、源码、实际配置、Data QA 离线行为、GLM
流完整性、已认证内部 HTTP、真实 UUID 只读路径和产品名称。不会再次调用模型
或新业务接口；新接口业务答案、统计问题及浏览器交互仍需单独验收。
控制器及引用扫描 13 项 Linux Python 3.7 检查通过，15 文件实际路径检查在隔离 QA
及 SDK 2.32.0 下通过。QA 初次单文件挂载缺少现有 llm_timing_context 模块；补全
同一工作树 harness 依赖挂载后通过，未改生产镜像来规避。现场候选镜像已独立通过。
