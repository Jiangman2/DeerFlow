# 中国海油全员制度知识库

状态：2026-10-24 已创建制度知识库和共享空间、确认 viewer 共享，并完成现有工作空间批量加入：新增 8,410 个 viewer，最终已有 8,411 个普通用户工作空间加入；已开通且符合条件的待加入工作空间为 0。自动加入已随 Gateway 387 上线，70 个副本全部通过源码、配置和健康检查。批量结束时尚未开通 WeKnora 的 76,331 名普通用户及未来新用户可在首次使用知识库时自动加入。浏览器可见性、真实制度检索问答和原生写请求拒绝仍需业务验收；生产源码锁已按成功回执更新。

现场制度库 ID：`bd83e659-9d9c-429a-acf1-ead01037cfa1`；共享空间 ID：`b9cfbf7f-7281-4f62-a72b-6a22379efe60`；归属工作空间：`10000`。创建回执归档为 `deploy/enterprise/weknora-company-bootstrap-field-20261024-r1.json`。创建操作已完成，不要重跑历史创建工具。

普通账号 `bianqy@cnooc.com.cn` 的现场候选验证已通过：添加一次 viewer 成员后，可在消费工作空间看到共享库并读取库元数据，再次访问无重复加入。原生有效权限返回 viewer；未执行写入拒绝探测、读取制度或模型问答。回执归档为 `deploy/enterprise/weknora-company-viewer-field-20261024-r1.json`。其附带全员统计返回 `RuntimeError`，未记录具体原因；这不否定已成功的成员验证，也不代表全员已加入。

独立 r2 现场核对已确定原因：已启用且完成初始化的平台账号有 **84,748** 个，超过旧统计工具 30,000 个扫描上限，在 `PLATFORM_COUNT` 阶段停止，尚未读取当前共享空间配置。该失败与原生加入接口的人数限制无关。证据见 `deploy/enterprise/weknora-company-members-field-20261024-r2.json`。

r3 现场完整分页统计已通过：84,742 个普通账号中，1 个已有 viewer，8,402 个现有工作空间待加入，76,339 个账号尚未开通原生工作空间；6 个平台管理员保持原权限。空间现有 1 个 admin、1 个 viewer；重新核验 `member_limit=0`、可搜索、免审批。身份歧义、重复邮箱和重复空间成员均为零。证据见 `deploy/enterprise/weknora-company-members-field-20261024-r3.json`；该工具没有创建服务器报告文件，也没有执行任何成员写入。

## 使用效果

管理员明确选择哪些共享空间面向全员；这些空间中的知识库统一以只读权限发布。首个资源为“中国海油制度知识库”和“中国海油全员制度共享空间”。现有及未来平台普通用户在首次访问知识库时，使用各自的 WeKnora 身份自动取得所选共享空间的 viewer 权限。在主对话输入框点击“知识库”，即可选择这些共享库，通过现有 WeKnora 检索、智能问答工具使用其中的资料。原有个人知识库和其他共享空间继续由原权限控制，不会因创建者是管理员就自动公开。

本版使用 Gateway 环境配置维护管理员选定的空间名单，尚未在同事维护的 WeKnora UI 添加“面向全员”复选框。新增一个全员空间需要更新该名单；向已选空间继续添加只读知识库无需修改名单或重新部署。空空间也可以先纳入，等管理员发布资料后再开展问答验收。

这里的“全员”指已获准登录企业平台的用户，不是匿名公开访问。原生 WeKnora 的可搜索空间也面向其已认证用户开放；启用前应确认该实例的账号和入口属于同一企业用户范围。

前端选择器已经支持共享知识库，因此本次上线无需修改前端或同事维护的 WeKnora UI，也不需要数据库加列。知识库按钮仍由用户选择库后启用问答，不会强制所有对话查询制度库。

## 首次资源创建

用户已授权且完成两个空资源的创建。`bootstrap-weknora-company-knowledge-host.py` 与 `bootstrap-weknora-company-knowledge-child.py` 是历史工具源码，已打包后在 datax-01 执行。工具绑定已核对的 Gateway 386、WeKnora App 82 及身份桥源码摘要。它以只读事务确认平台账号是启用状态的管理员，复用已有 SSO 凭据登录并验证原生管理员身份，然后通过 WeKnora 原生 API 完成四次资源写请求：创建知识库、创建共享空间、设置 viewer 共享、设置空间可搜索且加入免审批和不限成员数。

工具只选择该管理员拥有的工作空间，使用已有默认模型设置，不调用模型、不上传制度、不创建账号、不重置密码、不赋予管理员角色、不直接写数据库。原生登录及创建 API 自身会持久化会话或资源。创建后读回资源归属、共享权限和空间设置，返回知识库 ID、共享空间 ID、知识库页面相对路径及共享空间列表路径；不臆造不存在的空间详情页面路由。

同名资源、身份不符、配置漂移或响应异常均停止。已发出写请求而结果不明时不自动重试，保留 `/var/lib/portal-weknora-company-bootstrap-20261024-r1` 供只读核对。成功创建资源不代表所有用户已经自动加入；Gateway 候选发布及普通用户验收另行完成。

## 管理员日常维护

1. 用平台管理员账号进入“知识库”，创建知识库 **中国海油制度知识库**。描述可使用：`汇集中国海油现行规章制度，提供制度检索、条款查询和制度问答，由管理员统一维护。`
2. 在 WeKnora“共享空间”创建专用空间 **中国海油全员制度共享空间**。启用“可被搜索”，关闭“加入需审批”，成员上限设置为 `0`（不限）。若现场 UI 不提供这些选项，先完成只读版本核对，不应直接更改数据库。
3. 从制度知识库的“设置 → 发布集成 → 共享管理”将它共享到上述空间，权限选择 **只读 / viewer**。不要共享管理员的其他私有库。空间内新增的共享资源会对所有成员可见，后续添加资源应沿用同样的全员公开范围。
4. 上传需要发布的制度文件，确认解析和索引完成。空库可以验证可见性，不能证明制度问答可用。
5. 新增的全员空间向平台维护人员提供 UUID，由其纳入全员空间名单。管理员草稿库和受限空间不纳入；这些 ID 不是密钥，无需提供密码、API Key 或邀请码。首次两个资源使用上述创建工具时不必再手工重复创建。

原生“智能问答”与 DeerFlow 的 WeKnora 问答工具均可使用有权访问的知识库。如果另外创建了一个专门的自定义制度问答智能体，还需要在原生 WeKnora 中单独将该智能体共享为 viewer；仅共享知识库不会自动发布自定义智能体。

## 实现与配置

候选入口为 `backend/app/gateway/weknora_company_knowledge.py`，在 SSO 角色同步后、成功会话缓存前运行。配置仅来自 Gateway 的运维环境，不接受浏览器传入的空间 ID 作为全员授权依据。

| 配置 | 含义 |
| --- | --- |
| `WEKNORA_COMPANY_KB_ENABLED` | 默认 `false`；发布验收后启用 |
| `WEKNORA_COMPANY_KB_ORGANIZATION_IDS` | 管理员选定的全员共享空间 UUID JSON 数组，启用时允许 1–16 个且不重复 |

使用当前用户的 Bearer Token 和当前消费工作空间 `X-Tenant-ID`，依次读取名单中的空间、必要时调用原生 `POST /organizations/join-by-id` 请求 viewer、读回成员角色、核对原生 `/shared-knowledge-bases` 中属于所选空间的只读共享权限。绝不使用知识库所有者的工作空间代替消费工作空间，也不借用管理员 Token。不再将单个知识库 ID 作为全员策略配置。

已加入的 viewer 不重复加入。加入响应丢失时只读回一次，不重发 POST。空间不匹配、加入需审批、成员数限制、权限异常或网络异常均不会形成成功的 SSO 缓存。空空间正常通过，不影响后续发布知识库。整段调用最长 20 秒，单请求超时 5 秒，响应限制 2 MiB，关闭环境代理和重定向，保留默认 TLS 校验。错误只返回固定错误码和管理提示。

系统管理员保留原生管理权限，不走自动 viewer 加入，也不会因共享配置错误而被挡在管理界面之外。若普通账号已有该专用空间的 editor/admin 权限，候选会提示检查，不自动修改其既有授权。每次后续检索、下载、编辑仍由原生 WeKnora RBAC 校验，SSO 缓存不是授权依据。

本策略是强制全员加入：普通用户主动退出后，下次新 SSO 会话会重新加入；SSO 钩子只处理当前用户，现有用户的批量加入由下节独立工具处理。关闭开关只停止新的自动加入，不会撤销已经建立的原生成员关系。撤销制度访问应由管理员取消原生共享或移除权限，并重新核验，不应只回退镜像。

## 现有用户批量加入

用户随后明确要求把通用智能体的所有用户加入全员空间。现有已开通原生个人工作空间的普通用户可由管理员通过原生 `POST /organizations/{id}/invite` 添加 viewer 成员，不需要逐一登录，也不直接写成员表。尚未开通 WeKnora 的平台用户及未来新增用户由候选 SSO 自动加入处理，不会批量重置密码或创建额外工作空间。

正式批量工具准备前，先运行绑定 `bianqy@cnooc.com.cn` 的单用户候选验证。该工具在短时独立进程加载候选策略，最多发出一次 viewer 加入请求；再次访问必须无重复加入。原生 GET 返回的空间角色、有效权限、共享列表及知识库元数据均须确认 viewer。不会发出编辑或删除探测请求，因此不能把元数据检查表述成实际写请求拒绝测试。

同一工具附带只读人数统计，读取已启用且完成初始化的平台账号与原生工作空间成员元数据，输出待加入数量、已有 viewer、保留的高权限成员、未开通或身份不明确的数量，以及候选集合摘要；不输出用户清单、密码或文档内容。原生共享以工作空间为成员单位，先去重工作空间再统计批量写入数量。正式批量执行必须重新核对账号状态、归属、原生成员关系和该摘要；已有成员不重复加入、不降级，不确定的 POST 不盲目重发。

原生共享空间支持 `member_limit`；`0` 表示不限，正数按租户工作空间成员计数。首次创建后的 GET 已明确核验该全员空间为 `0`，无需通过修改原生服务代码移除人数限制。该配置与本地只读统计工具的 30,000 个平台账号扫描保护上限不同；超过扫描保护上限必须报告不完整，不能把截断的集合用于全员授权。

独立的 `inspect-weknora-company-members-r2-child.py` / `inspect-weknora-company-members-host.py` 仅复查统计，绑定创建和单用户成功回执，重新读取当前空间人数上限并返回失败阶段、固定错误码和脱敏计数。它保留原统计容量限制，未知角色值只计为 `other_or_null`；重复邮箱禁止生成批量候选。数据库查询均处于只读事务，单语句 10 秒、子进程检查 60 秒；不登录、重新加入或重写旧报告。历史 canary r1 不应重跑。

针对已核实的八万多账号，r3 改用 asyncpg 服务端游标，每批最多读取 1,000 行；每个数据库在自己的只读可重复读事务中扫描。平台总数与实际读取行数必须完全一致，使用同一集合参数查询相关原生身份和工作空间，避免逐用户查询或反复 OFFSET 扫描。平台扫描保护限额提高为 200,000 个，另限制原生匹配、成员记录和元数据字节数，超限或中途失败均不能产生完整候选。单语句 10 秒、整体 180 秒；不输出身份列表，不写入权限、不调用模型。两个数据库不是分布式一致快照，正式加入前仍须重新验证账号及权限状态。

r3 已通过 38 项分页、分类、隐私及主机状态回归检查，并在临时本机 PostgreSQL 16 中使用 84,748 个合成有效账号、85 个读取批次验证了真实 asyncpg 游标及 readonly 事务。原生侧使用 1,200 个合成身份以及停用、删除、挂起成员和已有高权限场景，完整分类得到 1,194 个合成待加入工作空间，成员表未变。这些数字仅是本地测试，不是线上 WeKnora 用户统计。运行脚本由 `package-weknora-company-members-r3.py` 生成。

上述 r3 为加入前统计。后续批量加入及自动加入部署均已完成，结果见下文；真实制度问答尚未验收。

### 10 月 24 日候选镜像回执与运行检查

五台 Gateway 节点（0018–0022）均返回预载成功，目标镜像为 `sha256:ba642b5484ad6103146de433432d24de4d0243ecbbdd251d2752e8aa961c5e30`。0021 使用只读命令补取原控制目录内的报告，没有重新导入。回执归档于 `deploy/enterprise/weknora-company-preloads-field-20261024-r1.json`。各节点报告均确认导入、解包、CRI 可见性以及保留旧引用；预载报告没有重新核验 Kubernetes Node UID，不能据此宣称现场 UID 已复查。

`package-weknora-company-image-check.py` 生成 `verify-portal-weknora-company-image-20261024-r1.py`，在 datax-01 使用 `--verify-preloaded-images-once` 执行。工具绑定 Gateway 386、WeKnora App 82、资源创建及普通用户验证的原始报告；新建一个短时 Job，在已预载的 0018 节点用确切摘要的候选镜像运行。创建前复查该节点 UID、状态和污点，使用 `imagePullPolicy: Never`。Job 不挂载生产配置、凭据或持久卷，不挂服务账号 Token，只挂临时 `/tmp`；进程显式拦截网络连接，全部 HTTP 调用使用内存模拟接口。

候选检查验证两个安装源码摘要、默认关闭、用户自身令牌与消费工作空间、只读加入及重复访问、多个所选空间与私有共享隔离、空间前置条件、已有高权限保留、非只读共享拒绝、响应丢失只读回不重发，以及真实 SSO 入口的失败缓存隔离和成功缓存。13 项运行检查在本机隔离进程通过，另验证源码变化拒绝；包装器 12 项检查在 Linux Python 3.7 全部通过。此处仍是本地验证，只有现场 Job 回执才能确认安装镜像通过；即便 Job 通过，也不表示已部署自动加入或完成制度问答业务验收。

批量加入工具仍在执行时，可以运行这个不接触生产数据的独立检查 Job。Gateway 滚动切换应等批量工具结束并核对报告后再进行，避免终止批量进程。没有完整回执时不重跑批量加入或预载工具。

现场 Job `portal-wk-company-check-4214e93398f7` 已使用上述确切镜像成功完成，13 项检查通过，29 次 HTTP 请求全部为内存模拟，没有生产数据库访问、模型请求或真实成员授予。原始报告为 `/var/lib/portal-weknora-company-image-check-20261024-r1/REPORT.json`（1,834 字节，SHA256 `e034af228dd8c0b01f6ae3f6b778effb9b59fb7f10ab03c69ef56c4040b79570`），操作员回执归档于 `deploy/enterprise/weknora-company-image-check-field-20261024-r1.json`。状态为 `WEKNORA_COMPANY_IMAGE_ACCEPTED_NOT_DEPLOYED`。

切换草稿位于 `deploy/enterprise/activate-weknora-company.py`，由 `package-weknora-company-activation.py` 打包。缺少 `weknora-company-enrollment-field-20261024-r1.json` 中的完整批量成功回执时，打包器拒绝生成正式工具；执行时还会绑定服务器原报告的大小和摘要，要求最终剩余目标为零、所有不确定 POST 均已读回确认。不会重发批量邀请。仅计划替换 Gateway 镜像并设置两个公司空间环境变量，其他环境变量、Secret/ConfigMap 引用、OA 配置及独立服务均保留。通过 UID、resourceVersion 与完整 spec 测试发出一次原子更新；请求超时只核对现场状态，不自动重试或回滚。滚动观察上限 90 分钟，超时不取消 Kubernetes 的滚动。

工具支持任务已结束和明确允许任务中断两种操作员选择，并读取当前活动任务计数。任一选择都不能绕过批量加入完成的前置条件。每个新 Gateway Pod 检查安装源码、两项生效配置与本地健康接口，再核对副本、服务端点、节点覆盖及其他部署/配置未变。WeKnora UI 使用执行时的快照保留同事的版本。草稿阶段通过 14 项 Linux Python 3.7 检查和 3 项本地运行检查；收到批量完成回执后，正式工具通过 18 项 Linux Python 3.7 检查，包含完整回执绑定、同大小内容篡改、符号链接和宽松写权限拒绝。资格记录见 `weknora-company-activation-preparation-20261024-r1.json`。

### 批量完成与正式切换

操作员提供的 `log(22).txt` 最后回执为 `BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED`。原始服务器报告 `/var/lib/portal-weknora-company-bulk-enrollment-20261024-r1/REPORT.json` 为 3,768 字节，SHA256 `502c7e55d6595a67f7d0233b8bae4e016cdc1aa7b905d8968a226d1db4b53a00`；本地证据归档为 `deploy/enterprise/weknora-company-enrollment-field-20261024-r1.json`。

工具用时 2,191.381 秒，214 批确认新增 8,410 个 viewer，8,410 次成员 POST 全部已确认，没有待确认写请求。最终 84,742 个合格普通账号中，8,411 个工作空间已有 viewer、76,331 个账号尚未开通，6 个平台管理员保持原权限。起始复查新增 6 个合格工作空间，处理期间又增加 2 个，三轮复查均已覆盖；因此 `matches_prior_readonly_inventory=false` 是正常的名单变化，不是批量失败。没有批量创建账号、修改平台角色、直接写数据库或滚动 Gateway。

`package-weknora-company-activation.py` 现已绑定此回执和候选镜像检查回执，生成 `activate-portal-weknora-company-20261024-r1.py`。操作员已明确接受现有任务可能中断，本次在 datax-01 使用：

```bash
sudo python3 -I -B -u /home/cnooc/activate-portal-weknora-company-20261024-r1.py --activate-company-knowledge-once --allow-active-task-interruption
```

该命令已完成 Gateway 386 → 387，更新到 `ba642b5484ad…` 镜像并设置公司空间两项配置，保留 OA 多文件开关、回调状态和其他环境设置。工具没有执行数据库迁移、重复批量邀请或修改 WeKnora App/UI。正式脚本及依赖源码摘要记录于 `deploy/enterprise/weknora-company-activation-package-20261024-r1.json`，属于历史发布材料，不要重跑。

操作员回传成功状态 `WEKNORA_COMPANY_AUTO_ENROLLMENT_RUNTIME_ACCEPTED_BROWSER_PENDING`，确认 70 个 Pod 验收通过、自动加入已启用、其他部署及配置元数据保持。Gateway 387 的 spec 摘要为 `82e248a2fb9f55f9e5426ee8787f42f98f2c3190a2a4c0b66c04aa934b79d64c`。服务器报告 `/var/lib/portal-weknora-company-activation-20261024-r1/REPORT.json` 为 42,134 字节，SHA256 `ab4b4c72b4b132d8588853f52c23e04171a52e27b1256ea727d840c4432602a1`；本地归档 `deploy/enterprise/weknora-company-activation-field-20261024-r1.json` 保留用户提供的摘要，不代表本地直接下载了完整报告。

摘要里的 `last_rollout_counts.terminatingReplicas=1` 是最后一次周期性进度，不是最终状态。脚本只有在 Deployment 完全就绪、旧副本退出、所有新 Pod 检查通过并最终读回后才报告成功。切换前读到 4 个活动 run，操作员已明确接受可能中断；本回执没有核验这 4 个任务的最终业务结果。源锁 `deploy/enterprise/production-source-lock-20261024.json` 现指向已接受镜像和 583 个 Gateway Python 源文件；上一版锁保留于 `production-source-lock-20261024-gateway386.json`。

### 分批执行工具

`package-weknora-company-enrollment.py` 生成 `enroll-portal-weknora-company-members-20261024-r1.py`，在 datax-01 通过 `--enroll-existing-company-viewers-once` 执行。工具绑定创建和单用户成功回执、Gateway 386 与 WeKnora App 82 基线。仅登录既有获授权管理员；不会逐用户登录、创建账号或重置密码。成员写入使用原生 `POST /organizations/{id}/invite` 的 `{tenant_id, role: "viewer"}` 请求。该 API 直接建立成员关系，不发送邀请邮件；成功响应仅含 `success`、`message`，不要求 `data` 字段。

执行前重新完整扫描，记录与 r3 候选摘要是否一致；原授权覆盖全部合格平台普通用户，因此正常新增的合格工作空间可纳入，不把旧摘要当作永远不变的名单。总写请求最多 10,000 次，超限在写入前拒绝。先加入并读回 1 个工作空间，然后每批至多 40 个；最多 2 个请求同时进行，开始请求间隔至少 0.25 秒。每批重新核对管理员、空间和 viewer 共享，并重新检查账号状态、身份、所有权及已有权限。慢响应导致验证超过 15 秒时，对下一对请求重新检查。已有 viewer 不重复添加，已有 editor/admin 不改动。

每对请求之后使用短时只读事务核对成员角色。响应丢失只读回、不重发；不是 viewer 或无法核对时停止。已尝试的工作空间不会在同一执行中重复 POST。最多三轮有界批量处理覆盖执行期间新开通的工作空间，结束前再做一次完整统计；只有无剩余待加入工作空间才报告本次完成。仍未开通的账号继续等待候选自动加入部署，不能把批量工具完成表述成未来自动加入已经上线。

工具持续输出脱敏批次进度，并在 `/var/lib/portal-weknora-company-bulk-enrollment-20261024-r1/` 保存 `STARTED.json`、`PROGRESS.jsonl` 和最终 `REPORT.json`。主机通过 stdin 心跳维持独立检查进程；连接断开或心跳丢失后停止后续请求，但最后一批请求可能已经提交。失败或中断后保留目录，不删除标记或重跑；先读取现有成员状态再准备续接。整体时限两小时，不重启 Gateway，也不修改部署或数据库结构。数据库查询均只读；原生 HTTP 登录与授权会写入各自会话、成员和审计记录。

此工具已通过 33 项本地回归检查、Linux Python 3.7 的六层内嵌源码编译和两项真实管道心跳测试。临时 PostgreSQL 与模拟原生 HTTP 联合验证了 48 个合成 viewer 写入、提交后响应丢失读回、停用账号拒绝、已有 editor 保留和重复执行零额外 POST；这些不是生产加入回执。

### 10 月 25 日管理员成员补齐（已完成）

现场执行已成功，回执为 `EXISTING_ADMIN_MEMBERSHIPS_ACCEPTED_ROLES_PRESERVED`。六个当前有效平台管理员均已有原生工作空间；执行前已有一个空间 admin、三个 viewer，另外两个缺少成员关系。本次保留四个已有成员，仅提交并确认两次 viewer 加入，最终为一个空间 admin、五个 viewer，缺失成员及尚未开通账号均为零。平台和原生系统管理员标志、已有成员权限均保持，没有重启 Gateway、修改部署或调用模型。此处 viewer 是共享空间角色，不表示系统管理员被降级。

服务器报告 `/var/lib/portal-weknora-company-admin-enrollment-20261025-r1/REPORT.json` 为 2,371 字节，SHA256 `e899fd4fc38d264fc200cd686ceba6cf6fb92161920549f7d1cf9350c6a37a6f`。用户回传摘要归档于 `deploy/enterprise/weknora-company-admin-enrollment-field-20261025-r1.json`，不是本地直接读取的服务器报告。无需重跑本次工具；管理员浏览器可见性及真实问答仍待业务验收。以下命令保留为已完成操作记录，后续新增管理员不在本次补齐范围内，原有管理员 SSO 旁路仍保持。

此前普通用户批量加入明确排除了平台管理员，自动 SSO 策略也保留管理员旁路。用户现已授权补齐其余管理员的空间成员关系，同时保留已有管理权限，并要求兼容已经手动加入的情况。此项使用独立的一次性工具，不改自动 SSO 策略，不重新预载或滚动 Gateway。

`package-weknora-company-admin-enrollment.py` 生成 `enroll-portal-weknora-company-admins-20261025-r1.py`。在 datax-01 执行：

```bash
sudo python3 -I -B -u /home/cnooc/enroll-portal-weknora-company-admins-20261025-r1.py --enroll-company-admins-once
```

工具绑定已验收的 Gateway 387、WeKnora App 82 和服务器上的 Gateway 自动加入上线原报告。重新查询当前启用且完成初始化的平台管理员，并匹配其现有原生系统管理员身份、所拥有的默认工作空间。平台管理员上限为 32 个；无原生身份的账号计入尚未开通，身份歧义、原生管理员标志异常或工作空间归属不明确时拒绝继续。不会创建账号、重置密码或为管理员改写身份标志。范围不包含仅存在于原生 WeKnora、没有对应平台管理员身份的账号。

已存在的空间 admin、editor、viewer 成员均跳过写请求并保留原角色；缺失成员仅通过原生邀请接口添加 viewer。空间成员角色与平台、WeKnora 系统管理员权限分别处理，新增 viewer 不撤销后两者的管理权限，也不自动提升为空间管理员。只登录既有空间创建者，不逐个登录待加入管理员。成员写请求最多 32 次、逐一执行；每次请求前重新核对身份、工作空间归属、已有成员角色和该空间的只读知识库共享。只读数据库事务在 HTTP 请求前结束，不跨网络调用持有事务。

手动加入与工具执行发生竞争时，原生接口不会覆盖既有成员，工具读回实际角色并保留；提交后响应丢失时同样只读回，不重发请求。执行期间已观察到的高权限被降低或身份状态改变时停止。结尾再次核验所有目标，只有没有缺失成员、没有尚未开通账号且权限核验通过才报告 `EXISTING_ADMIN_MEMBERSHIPS_ACCEPTED_ROLES_PRESERVED`。若有尚未开通账号，已符合条件的成员补齐结果会保留，但整体报告不完整，不能表述为全部管理员已加入。

执行记录保存在 `/var/lib/portal-weknora-company-admin-enrollment-20261025-r1/`。工具无需输入密钥，不读取文档或聊天，不调用模型，不修改部署、配置或数据库结构；原生登录和成员 API 会写会话及成员记录。失败或中断后保留报告，不删除一次性标记或重复执行，先核对报告再安排续接。

已通过 24 项本地回归、8 项 Linux Python 3.7 检查及 Ruff。另在独立的临时 PostgreSQL 16 中验证六个合成管理员的真实只读查询，以及两次模拟成员 API 写入：保留并发加入的 editor、确认提交后丢失响应的 viewer；第二次执行核心逻辑没有额外 POST，平台和原生管理员字段均保持原值。临时容器已清理。这些是本地测试，不是线上管理员加入回执。正式脚本及依赖摘要见 `deploy/enterprise/weknora-company-admin-enrollment-package-20261025-r1.json`。

## 发布与验收

先运行 `deploy/enterprise/inspect-weknora-company-knowledge.py --inspect-readonly`，核对生产 Gateway、WeKnora 镜像、原生共享表结构及上述两个确切名称的资源 ID。脚本不登录用户、不创建知识库、不授权、不读取制度正文和用户记录；数据库访问位于只读事务。此检查不证明原生 API 的运行契约，现场还需验证。

本地源码参考是仓库中的原生共享接口；现场 WeKnora 镜像构建来源尚待确认，不能把本地源码检查当成生产接口已验收。启用前应以一名获准测试的普通用户验证所选空间的 GET、viewer 加入和共享库列表形状，再准备 Gateway 候选镜像与上述两项配置；不要直接使用本示例开启线上功能。

2026-10-24 候选镜像已从当前 Gateway `0a47db87…` 追加一层构建，仅含 `weknora_company_knowledge.py` 和 `weknora_identity.py`。打包时移除精确的 SSO hook 后，身份桥文件必须仍匹配现场 `af64e8d9…` 源码摘要；策略文件必须匹配普通用户现场 canary 的 `b0462680…`。候选镜像摘要为 `sha256:ba642b5484ad6103146de433432d24de4d0243ecbbdd251d2752e8aa961c5e30`。68 个原层和全部运行配置保持，只有新的第 69 层及构建历史追加。构建锁见 `deploy/enterprise/weknora-company-preload-build-20261024-r1.json`。

预载脚本 `preload-portal-gateway-weknora-company-20261024-r1.py` 仅允许五台 Gateway 节点 `ecs-zdz-pro-0018` 至 `0022`，参数为 `--preload-gateway-weknora-company-once`。已通过 12 项 Linux Python 3.7 预载输入、节点覆盖、摘要篡改拒绝、旧引用保留和配置不变检查。预载不设置环境开关、不加入成员、不滚动 Gateway、不修改 WeKnora App/UI；后续安装镜像运行验证和 Gateway 部署已完成，未来首次访问的浏览器验收仍待确认。生产源码锁已按后续上线回执指向该镜像。

现场验收至少包括：现有普通账号和新普通账号能在知识库按钮里看到制度库；管理员能上传维护；普通账号可检索问答且不能编辑、删除或向专用空间发布其他库；用户自己的私有库没有对他人开放；同一用户再次访问不产生重复成员；系统管理员仍可修复配置；取消原生共享后访问被拒绝。制度问答还需使用真实已索引文件校对答案和引用。若需要自定义智能体，另验收其共享权限和调用。

候选相关本地测试位于 `backend/tests/test_weknora_company_knowledge.py` 和 `backend/tests/test_weknora_company_bootstrap.py`，并复用身份桥、共享列表和工具鉴权回归测试。覆盖多空间、后续新增库、私人共享不变、空空间、权限异常、管理员归属、只读查询、原生资源写请求边界及不确定创建不重发。测试通过不等于线上已开通。
