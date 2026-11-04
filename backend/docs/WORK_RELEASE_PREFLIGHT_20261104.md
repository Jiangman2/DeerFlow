# Work 分流与日历任务升级：上线前核验

本轮将已在本机测试的 R4 增量三方合入最新 `0716`：
`ef4ce7a177a92750ceb780eb8725814328bade3d`（2026-11-04 拉取）。
候选分支为 `codex/work-release-preflight-20261104`。这是源码候选，未合并、未部署生产。
原 R4 工作树、镜像和中午 R1 后备保留；11:32已切换本地R5，用户随后粗测反馈效果改善并授权提交本版PR，详见验证记录。
本轮源码、隔离PostgreSQL、三进程和Docker恢复的实际结果见 [11月4日验证记录](WORK_RELEASE_VALIDATION_20261104.md)。

## 此次部署范围

- 更新 Gateway：可信消息来源、Work 语义分流、日历草稿、多轮补问、有限批量创建、任务版本及确认屏障。
- 本增量没有新增表、列、索引、Alembic revision 或依赖升级。已有 `work_integration_records` 复用加密 JSON。
- 生产应已经具备 `0022_work_integration_state`。先只读确认实际 revision、表和可解密的接收租约；
  若不符，停止此次升级，另行评估首次 Work 接入迁移，不能靠 stamp 或删除旧表解决。
- 已兼容 0022 的 PPT Runner 本次无需重建。Frontend、Sandbox、WeKnora、SkillHub 不属于此次 Work 增量的更新对象。
  最新生产分支中同事的其他改动必须保留，不能用旧 R4 的整个源码目录覆盖生产。
- 删除、分享、邀请参会人、会议室和周期日程仍关闭。有限批量最多 10 条、相同钟点和时长，不能代替真正周期规则。
- 本地 AMD64 测试镜像不能充当生产 ARM64 镜像；按生产实际架构构建不可变 tag，记录 digest。

## 先做只读检查

仓库提供 `deploy/enterprise/work-runtime-preflight-20261104.py`，可将脚本通过标准输入交给每个
Gateway 内的 Python，先用默认参数，再按需追加 `--database`、`--probe-network`。脚本不启动渠道、
迁移、模型、OAuth 或真实用户任务；网络探测仅获取应用 token 并读取机器人信息，不发送机器人消息。
输出只含配置摘要、指纹、状态码和白名单错误中明确报告的 IP，不含 Secret/token/原始错误正文。

示例（**由运维填入并核对生产 context、namespace、Pod；本次未执行生产命令**）：

```bash
kubectl --context <生产context> -n <namespace> exec -i <gateway-pod> -c gateway -- \
  /app/backend/.venv/bin/python - --database --probe-network \
  < deploy/enterprise/work-runtime-preflight-20261104.py
```

首次先选实际接收器所在 Pod 和各不同节点/出口组代表，再对全部 70 Pod 做默认配置核对。
如果各 Pod 的网络策略、节点出口或代理来源不同，应覆盖每一组，必要时逐 Pod 探测；
单 Pod 成功不能推断全部 Pod。默认检查不发网络请求，`--database` 仅 SELECT。
健康探针和网页“已连接”都不能替代两条 HTTP 路径的成功结果。

全部 Pod 必须一致的项目：

| 项目 | 必须满足 |
| --- | --- |
| 镜像/进程 | 同一候选 digest，单进程；避免在同一 Pod 再加多个 Gateway worker |
| 配置 | `DEER_FLOW_WORK_ENABLED=true`、`DEER_FLOW_WORK_PROFILE=production`、SQL 持久化；正式 App ID，不是 UAT App ID |
| 凭证来源 | 网页保存的配置优先于环境引导值；不能只改 ENV 后就认定已切换应用 |
| 数据与密钥 | 同一 PostgreSQL 库、0022、相同稳定 `DEER_FLOW_WORK_STATE_KEY`；不能每 Pod 启动时生成新 key |
| 共享文件 | 同一份 NFS runtime-config 和用户数据，实际验证跨 Pod 文件锁/CAS；路径相同不等于挂载相同 |
| API | `https://open.w.cnooc.com.cn`，渠道 SDK domain 与 Work API origin 一致 |
| 文档 | `hnwork.w.cnooc.com.cn`；不放入 UAT 文档域名 |
| OAuth | `https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback`；入口路径为 `/api/integrations/work/oauth/authorize` |
| 模型 | 已批准且支持结构化工具调用的当前平台模型；沿用平台默认或显式 `DEER_FLOW_WORK_DOCS_MODEL`，不加新供应商 |
| 限时 | 推荐明确配置 `DEER_FLOW_WORK_TURN_TIMEOUT=90`、`DEER_FLOW_WORK_TURN_QUEUE_TIMEOUT=10`；旧 ENV 的 30 秒会覆盖新版默认 |

App Secret、状态 key、数据库密码均在受控 Secret 中管理，不贴入 PR、截图或聊天。
摘要指纹仅用于对比一致性，不是凭证也不能用于恢复原值；必须结合实际 NFS 挂载和数据库配置核对。

ITC 本轮无需增加新 scope；继续核对正式应用已发布的用户身份权限
`docx:document`、`calendar:calendar:readonly`、`calendar:calendar`，以及正式回调和服务器出口白名单。
“请求到 OAuth 链接”不等于应用权限已开通，用户完成授权也不替代云文档自身 ACL。
同一个 App 若同时在同事电脑或其他独立数据库环境连接，SQL 租约无法跨库协调；
生产和 UAT 应分开使用应用，测试应用也需确认只有约定的接收环境。

## 出口和代理：必须在生产 Pod 内确认

目前两条路径的实现不同，属于既有行为：

1. 文档/日历客户端 `WorkAPI`：`httpx.AsyncClient(trust_env=False)`，忽略 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`。
2. SDK 的 HTTP 回复/握手请求：沿用 SDK 默认 transport，会受环境代理和 `NO_PROXY` 影响。

环境代理策略不同可能带来不同出口；即使两条请求都未选择显式 HTTP 代理，也不能据此认定出口相同。
本机11月4日早先观测中，两条请求的环境及Session合并均未选代理，但Work仍报告不同IP。
后续Docker日志确认应用请求仍被系统透明代理转送；11:05补入五个精确Work主机的Windows系统代理绕过后，
Docker新请求转为direct/bypass，两条API均200/code0，用户确认Clash开启时机器人正常回复。
这定位了本次本机失联的代理层；两个历史公共IP之间差异的更细网络成因没有单独证明，不能直接归因为`trust_env`。
本机Clash设置不属于生产代码，正式服务器应独立核验。只验证 token 接口、SDK 长连接或本机公网查询都不够；
token 接口成功也不能证明带白名单保护的 API 成功。错误 `99991401` 中的 IP 才是该次受保护请求的观测值。
没有白名单错误时，脚本不会猜测出口 IP，应由网络侧 NAT/代理日志确认稳定出口。

**先确定生产所需路径，再改配置**：

- 若生产允许直接访问两个 Work 服务 origin，可让 SDK 同样直连：在保留原有条目的基础上，
  为相关代理实现配置精确主机 `open.w.cnooc.com.cn`、`accounts.w.cnooc.com.cn` 的 `NO_PROXY`/`no_proxy`，
  两个大小写版本保持一致，重新检查实际 SDK 和 WorkAPI 路径。不能用 `*` 或直接移除全局代理，
  以免影响模型、搜索或其他渠道。
- 若 SDK 已绕过环境代理，继续增加同一个 `NO_PROXY` 条目不能证明会固定出口；
  应由网络侧核对域名解析、NAT/透明代理和实际连接路径，确定可持续使用的出口及白名单范围。
- 若单位要求经过专用代理，现有 WorkAPI 不会因设置全局代理而走代理。必须先设计仅限 Work 的显式 transport 配置并复验，
  不可声称设置 `HTTPS_PROXY` 就已统一，也不应临时在客户端关闭 TLS 校验。
- 两路暂不统一时，ITC 必须覆盖两路实际且稳定的服务器 NAT/代理出口；有多个节点出口组应全部覆盖。
  不使用笔记本/手机热点 IP 代替生产出口，不凭 `10.63.151.*` 的前缀推断可用白名单格式或安全范围。
- SDK WebSocket 的持续连接还需单独看租约与真实接收状态；HTTP 探测不会另外建立 WebSocket。

这轮准备不会擅自修改全局代理。生产配置、路由及实际出口尚需运维在服务器执行上述检查并提供脱敏结果。

## 70 Pod 升级与维护窗口

**不能把 `DEER_FLOW_WORK_ENABLED=false` 当成已有 Work 应用的暂停开关。** 当前关闭集成时接收租约不会创建，
但已配置且启用的 Feishu 渠道仍可能按普通渠道启动 WebSocket，造成多个 Pod 同时连接。
网页删除/断开整个渠道配置也不是无损暂停：它可能撤销用户绑定。

新旧任务协议不能在仍有旧进程处理 Work 的情况下混跑。此次代码没有独立的无损 Work 维护/批量能力灰度开关。
现有 `DEER_FLOW_WORK_ALLOWED_OWNER_IDS` 只限制文档/日历业务授权和执行，**普通消息的模型规划在此门槛前发生**；
它不能承担流量隔离，也不能仅限制新批量功能。生产全量用户可用时留空，并清除兼容旧名单的影响。

可采用运维批准的停机维护窗口：

1. 记录现有镜像 digest、70 副本、配置与 Secret 版本；备份数据库并单独保管状态 key。
2. 停止对外接收新的 Work 业务。需要实际的渠道上游暂停能力或停服务，不能仅口头通知用户、关闭新 OAuth 链接或改 owner 名单。
   当前没有独立应用内维护开关；若上游不能可靠暂停，应按整组 Gateway 停机安排窗口，平台 API 也会暂时不可用。
3. 能可靠暂停接入时先等已认领写入完成并核对回执；停止所有旧 Gateway 及其后台任务，再确认旧 Pod/旧连接退出。
   若不能先 drain 而必须终止进程，可能中断已发送的写入；保留 executing/uncertain 记录并人工核查，绝不自动补发。
4. 全部旧进程停止后，在副本为 0 时更新 Gateway 镜像/明确配置，再恢复 70 个兼容新协议的 Pod。
   不使用普通新旧交叠的滚动策略来证明此次协议安全。不要重建/清空 NFS 或数据库。
5. 核对全部 Pod 同 digest、同 SQL/key/config，只有一名有效租约持有者；其他 Pod 应为 HTTP sender standby。
6. 恢复入口后先做业务小范围验收。owner 名单可限制这部分业务使用人，但其他用户的普通 Work 对话仍会经过语义判断，
   因而这不等于独立流量灰度。真正需要流量灰度应另设独立应用/环境或经过验证的入口门槛。

这是待运维审阅的发布步骤，不是已经执行的生产操作。70 Pod 不等于已验证 140 路模型吞吐；
普通入站规划集中于接收器进程，OAuth 后续也可能在回调落点执行，必须结合真实流量评估模型队列和超时。

## 回退和最终验收

- 回退也要先暂停接入、停止全部新写入进程，保留有效 grant、渠道绑定和 executing/completed/partial/uncertain 回执。
- 升级前旧授权链接和预览在新协议中被拒绝，用户重新发起；已有有效授权仍按主体和 scope 复用。
- 旧生产镜像不能直接续跑新的草稿/批量协议。需在专门演练后，使未执行的 job/state/preview/conversation 失效，
  再启动旧版；不能清空 Work 表、回滚整个生产数据库或让旧进程和新进程并写。
- R4 本地后备与同协议版本的回退证据不等于“回退旧生产协议”已演练。本轮未在生产执行任何状态失效或数据修改。

最终上线验收至少包括：

| 场景 | 通过条件 |
| --- | --- |
| 来源隔离 | Work 语义补问可用；网页新会话及从网页续聊 Work 历史线程仍走原流程，不能伪造 Work 身份 |
| A/B 用户 | 分别授权；不能互用 token、文档 ACL、预览码、浏览器回调或绑定 |
| 自然语言与续答 | 读取/看看/查询等表达；缺日期、标题、时长时只补问缺项，已有“三天”等条件保留 |
| 换题 | 普通知识问答可退出旧任务；旧链接/确认码不能恢复任务，个人有效授权保留 |
| 日历写入 | 单条/3 条有限批量预览正确；本人确认才写；真实回读核对日期、条数、时长 |
| 编辑与失败 | 实时定位，歧义先选；禁止删除和邀请；部分成功/未知结果不重放整个批次 |
| 多 Pod | 授权入口和回调落不同 Pod；重复确认唯一执行；接收器退出后合法接替，旧接收器拒绝分发 |
| 网络和容量 | 两条路径、所有出口组通过；监控 receiver lease、模型排队/超时、API code；检查代表性并发 |

离线测试、合成模型规划、3 进程共享 PostgreSQL、NFS 实际挂载验证、真实 Work 验收和 70 Pod 容量是不同证据，不能互相替代。
离线回归和准备脚本不执行真实用户文档/日历读写，也不追加专门的合成模型验收轮次；用户自行UAT试测结果另记。
