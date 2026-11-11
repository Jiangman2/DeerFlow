# 海能 Work 生产接入与本地 UAT 开发

2026-11-04 基于最新0716的增量升级应使用 [上线前核验](WORK_RELEASE_PREFLIGHT_20261104.md)。
该文区分首次接入和已有70 Pod升级，包含只读出口/代理检查及维护窗口限制；本次增量无新DDL。

2026-11-02 的语义分流、每轮来源和任务退出候选见 [WORK_ROUTING.md](WORK_ROUTING.md)。
该增量复用本文件的既有状态表，**不新增表、字段或数据库迁移**；下文0022建表说明属于首次Work接入。
同日后续的日历草稿、有限批量和纠错候选见 [WORK_CALENDAR_TASK_AGENT.md](WORK_CALENDAR_TASK_AGENT.md)，删除仍保持关闭。

## 本次边界

新增生产配置、Gateway 浏览器回调、共享加密状态和迁移 `0022_work_integration_state`。
2026-10-28 已整合 0716 基线 `3c81ad48`：现有 `0020_ppt_confirmation`、`0021_oa_result_sets`
保持原样，Work 接在 0021 后，不能照抄 10 月 22 日旧候选的迁移编号。
**代码候选，不代表已发布、已获得生产权限或已通过真实生产机器人验收。**
功能仍默认关闭。默认 profile 是 `production`；UAT 必须显式选择。
文档删除/分享、日程删除、参会邀请和会议室操作仍未开放，不因 broad scope 授权而解禁。
分工、发布顺序及验收门槛见 [WORK_RELEASE_CHECKLIST.md](WORK_RELEASE_CHECKLIST.md)。
本轮回归、三实例故障测试及其证明边界见 [WORK_VALIDATION_20261028.md](WORK_VALIDATION_20261028.md)。

## 正式回调地址（交 ITC 登记）

```text
https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback
```

浏览器入口 `/api/integrations/work/oauth/authorize` 和回调随 Gateway 启动，不需要
Windows 本地 relay、10086 端口、端口转发或 Sandbox。授权后仍验证原消息的 Work
open_id、平台 owner、connection、workspace/chat 及当前持久化绑定；只支持已绑定私聊。
OAuth 与平台 SSO 登录是两套不同的授权。

Nginx 只为这两个精确路径设置路由/限流和 query 日志屏蔽；Gateway 只豁免这两个
精确路径的平台登录检查，仍验证一次性 state 和 Secure/HttpOnly/SameSite=Lax cookie。
上游入口网关、WAF、APM 同样必须屏蔽这两个路径的 query，不能记录 code/state/ticket。
后端 ASGI 中间件在发响应前清除访问日志 scope 的 query；页面无第三方资源、无 token。
**不得为了让回调通过而公开整个 `/api/integrations/` 或关闭全站认证。**

## 管理员网页配置仍然有效

凭证优先级：已保存的网页渠道配置 → 原渠道配置文件 → 当前 profile 的环境变量引导值。
环境变量只在渠道没有凭证时引导，不覆盖已保存的 App ID/Secret，也不复活网页中停用的渠道。
不要只设置半组凭证。网页保存失败启动时，返回 503 并保留已保存配置；不会覆盖其他管理员
已经提交的更新。配置仍沿用现有共享 runtime-config 存储和 CAS 锁，所有 Gateway 必须
挂载同一份可用存储。服务级协调任务约每 4～5 秒核对共享配置，渠道已停止时仍会
检查重新启用；不是即时跨集群配置广播。明确停用会保留停用标记，不回退旧启用缓存，
也不由环境变量中的引导凭证复活。读取失败不使用缓存启动新连接。

生产/SQL Work 的协调任务随 ChannelService 启动，独立于接收器及用户网页请求。
数据库或配置异常后自动重试，指数退避带随机抖动，最长间隔 60 秒，成功后恢复正常
检查间隔。初次连接失败也保留协调任务。所有启动、停止、网页修改共用生命周期锁，
未能完成 SDK 关闭/线程退出时保留旧实例，禁止覆盖并创建第二条连接。
失去数据库租约权威时立即拒绝绑定和消息分发，由协调任务停止/恢复连接。SDK 已关闭
后释放 SQL 租约失败只记录固定诊断，等待原租约到期，不强制删除其他实例的租约。
因此数据库恢复或接收进程突然退出后，接替仍可能等待 60 秒租约到期及协调重试；
进程健康接口可用不代表 Work 接收已恢复，监控须另外检查渠道和接收租约状态。

Secret/App ID/profile 改变时，旧集成实例关闭并重建；状态命名空间包含应用凭证代次。
旧授权链接/待确认操作不会误用新凭证，用户需要重新发起授权/预览，但不自动清空渠道绑定。
同一凭证下正常重启不清除共享 token 和未过期预览。切换应用/租户后原绑定能否继续匹配
由真实 Work 身份和平台绑定检查决定，不能用旧 token 兜底。

## 生产配置

以下值放运行环境或部署 Secret，**真实 Secret、token、加密密钥不得提交 Git**。
`k8s/enterprise/runtime-patches.yaml` 固定生产 profile 和正式回调，仍不自动启用功能。

| 变量 | 含义 |
| --- | --- |
| `DEER_FLOW_WORK_ENABLED=true` | 配置齐备并验收后显式启用 |
| `DEER_FLOW_WORK_PROFILE=production` | 生产默认值；企业 overlay 显式设置 |
| `DEER_FLOW_WORK_PRODUCTION_APP_ID` / `...APP_SECRET` | 可选引导值；网页已存凭证优先 |
| `DEER_FLOW_WORK_PRODUCTION_CALLBACK_URL` | 上述正式 HTTPS 地址 |
| `DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST` | 默认 `hnwork.w.cnooc.com.cn`，已由用户提供的正式链接确认；可配置精确主机名，不接受 UAT 域名 |
| `DEER_FLOW_WORK_PRODUCTION_API_URL` | 默认 `https://open.w.cnooc.com.cn`，仅受控服务端配置 |
| `DEER_FLOW_WORK_PRODUCTION_AUTHORIZE_URL` | 默认 `https://accounts.w.cnooc.com.cn/open-apis/authen/v1/authorize` |
| `DEER_FLOW_WORK_STATE_KEY` | 必填：32 随机字节的 URL-safe Base64 编码；所有实例一致 |
| `DEER_FLOW_WORK_ALLOWED_OWNER_IDS` | 可选的文档/日历业务能力名单，不限制渠道连接；普通消息在名单校验前仍可能调用规划模型，不构成流量灰度；全量开放时设为空，仍须有效绑定和本人授权 |
| `DEER_FLOW_WORK_MAX_JOBS` / `...PER_USER_JOBS` / `...CONCURRENCY` | 活跃任务总量32、每人4、每进程执行并发2；部署总模型并发另行评估 |
| `DEER_FLOW_WORK_DOCS_MODEL` | 可选显式绑定现有批准模型名；不设置沿用平台默认 DeepSeek V4 |

密钥必须由运维安全生成并写入 Secret，例如在受控终端使用
`base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()`；这不是应用 Secret。
密钥不能每次启动重生成，不可未经迁移直接轮换；数据库备份与密钥分别保管。
当前不保存 refresh_token、不自动刷新；access_token 到期后重新授权，不需要解绑渠道。

全量开放不需要枚举所有平台用户。显式设置 `DEER_FLOW_WORK_ALLOWED_OWNER_IDS=""`，
或同时移除它和兼容变量 `DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS`，避免回退读取旧测试名单。
UAT 仍要求明确测试人员；生产空名单不放宽发送者核验、token 隔离、资源 ACL 或写入确认。

## 存储、配额与执行

Gateway 正常数据库启动路径自动执行 Alembic 迁移；本次新增一张独立表，不改现有绑定表。
发布前备份数据库并演练从实际版本升级，至少覆盖现有 0021。多实例生产必须使用共享 PostgreSQL；SQLite
仅用于单节点开发/离线测试，不承诺跨节点文件锁可靠性。

所有共享此平台库且执行 Alembic bootstrap 的服务都必须兼容 0022，包含专用 PPT Runner。
正式 Runner 可使用 `docker/ppt-master-runner/Dockerfile.production-work-overlay`，
以 `deploy/enterprise/production-source-lock-20261023.json` 中的 Runner 55 镜像为经核验基底，
只补入 0021/0022 迁移、对应 OA/Work ORM 和注册入口，保留原 Runner 应用/Agent。
不能回到历史 R3 基底，也不能覆盖封存的 Runner 源码快照。ORM 也必须包含，
否则空库可能缺少 Work 表或 OA 新字段，却直接 stamp 到新 head。
产物必须使用新不可变标签，重新记录实际架构、digest 和验收结果；历史 lock 不是新镜像回执。
不必把 `production-source` 中的整个历史 harness 复制覆盖到当前 Gateway。

AES-256-GCM 加密状态，并把命名空间、记录种类、随机标识及主体身份绑定到认证数据。
token 不进入模型、Skill、Sandbox、浏览器、普通日志或配置文件；只进入加密数据库记录。
OAuth job/state、确认预览、去重标记、少量定位上下文共享保存；绑定每次通过现有绑定仓库核验。
容量在 SQL 事务内串行判定，已完成任务不占活跃配额，单个用户不能占满整个队列。
执行并发2是每进程值，不应解释为全平台最多2个模型请求。

Work理解请求的排队与模型执行分开限时：`DEER_FLOW_WORK_TURN_QUEUE_TIMEOUT` 默认10秒、范围1～30；
`DEER_FLOW_WORK_TURN_TIMEOUT` 默认90秒、范围5～180，显式旧值30继续有效。
模型总预算覆盖分类及日历领域步骤，队列满时返回明确繁忙提示；服务额外1秒watchdog不提供重试额度。
该参数不是Work API写入重试配置。日志中的消息哈希、阶段和耗时用于关联定位，不记录用户正文或模型原始输出。

预览5分钟、授权链接10分钟；SQL CAS 一次性认领授权/写入，同一用户的写入有共享租约。
执行前复查 token、绑定及最新目标，写入后回读；Calendar 上游没有通用原子 CAS，用户在
Work 客户端同时编辑仍存在检查到 PATCH 之间的竞态，不能宣称完全消除外部并发修改。
已执行/不确定操作不重新提交；执行中进程崩溃也保留非可重放记录，用户再次确认时提示核查。
处理中读取任务崩溃不会自动续跑，用户可重发请求；这里不是通用持久化 Agent 调度队列。

待确认 payload/预览/旧目标仅短期加密保留，认领写入时替换为最小执行元数据。
审计记录 actor、操作、目标ID、确认ID、时间、结果和回执，保留90天；不存 token/全文。
授权过期和短期记录在后续请求时清理，审计清理随保留期处理。

## 长连接生命周期

生产共享状态模式下，SQL 租约按 profile/API/App ID 控制接收器，不按用户数计数。
只有持租约实例启动 WebSocket；其他 Gateway 初始化 HTTP 发送器，所以回调落到其他
实例也能回机器人。租约60秒、每5秒续约；收消息和消费绑定码前重新核验租约。
退出时先关闭 socket/SDK任务并 join 线程，再释放租约；清理失败不创建第二个接收器。
SDK 任务工厂阻止停止期间新建 ping/select 任务，处理握手结束与 stop 之间的竞态。

租约不是 Work 服务器的强制 fencing 协议；进程长时间暂停可能出现短暂残留 socket，
但失去租约的实例不处理新消息。首轮灰度建议单接收实例，并实测停机/滚动发布/租约故障。
HTTP发送请求成功与用户客户端最终展示仍有差别，必须做真实 Work 验收和出口白名单检查。

## 保留本地 UAT

旧本地启动脚本和 `localhost:10086/auth/callback` relay 保持兼容，只能在 UAT profile 使用。

```text
DEER_FLOW_WORK_PROFILE=uat
DEER_FLOW_WORK_ENABLED=true
DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS=<明确的开发测试平台用户ID>
DEER_FLOW_WORK_DOCS_RELAY_KEY=<本地独立随机中转密钥，至少32字符>
```

UAT 默认文档主机 `haiyoutest.w.cnooc.com.cn`，旧 `DEER_FLOW_WORK_DOCS_UAT_ENABLED=true`
仍可兼容选择 UAT。凭证继续在本地网页配置，或用 `DEER_FLOW_WORK_UAT_APP_ID/APP_SECRET`
引导。UAT 默认使用内存，重启需重新授权；可显式 `DEER_FLOW_WORK_STORE=sql` 测试持久化，
此时也必须设置独立 `DEER_FLOW_WORK_STATE_KEY`。不要将本地中转密钥部署到生产。

## 日程确认卡片与文件读取

在海能 Work 应用的「回调配置」中订阅并发布 `card.action.trigger`。卡片的接收方式应与应用后台一致。常规消息仍走「事件配置」的长连接，浏览器授权仍走原 OAuth 回调。

| 功能 | 启用条件 |
| --- | --- |
| 日程确认卡片（长连接） | Work 使用共享 SQL 存储。设置 `DEER_FLOW_WORK_{UAT\|PRODUCTION}_CARD_ENABLED=true` 和 `DEER_FLOW_WORK_{UAT\|PRODUCTION}_CARD_TRANSPORT=websocket`，在应用后台选择「使用长连接接收回调」。卡片沿用当前 Work 渠道 App ID 和接收租约。 |
| 日程确认卡片（HTTPS） | 在应用后台选择「将回调发送至开发者服务器」，登记 Work 服务器能访问的 `POST /api/integrations/work/card/callback`。设置对应环境的 `CARD_ENABLED=true`、`CARD_TRANSPORT=http`、`CARD_APP_ID`、`CARD_TENANT_KEY`、`CARD_VERIFICATION_TOKEN` 和 `CARD_ENCRYPT_KEY`。验证令牌和加密密钥从部署 Secret 注入。旧配置省略 `CARD_TRANSPORT` 时按 HTTPS 处理。 |
| 在线表格、多维表格、云盘文件 | 设置 `DEER_FLOW_WORK_FILE_READ_ENABLED=true`。为对应 profile 配置 `DEER_FLOW_WORK_{UAT\|PRODUCTION}_SHEETS_READ_SCOPE`、`DEER_FLOW_WORK_{UAT\|PRODUCTION}_BITABLE_READ_SCOPE` 和 `DEER_FLOW_WORK_{UAT\|PRODUCTION}_DRIVE_READ_SCOPE`。分别使用 ITC 确认已启用的用户身份只读权限。 |
| 按文件名读取 | 此功能默认关闭。ITC 在对应环境的 Work 应用发布用户身份 `drive:drive:readonly`，并先在 UAT 验证 `POST /open-apis/suite/docs-api/search/object`。开启时设置 `DEER_FLOW_WORK_FILE_SEARCH_ENABLED=true`，并按环境配置 `DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE=drive:drive:readonly` 或 `DEER_FLOW_WORK_PRODUCTION_FILE_SEARCH_SCOPE=drive:drive:readonly`。已确认支持新版搜索的环境可继续显式配置 `search:docs:read`，调用 `POST /open-apis/search/v2/doc_wiki/search`。内容读取权限按实际文件类型单独配置。 |

长连接回调通过当前持有效接收租约的连接接收，HTTPS 回调单独验签。两种方式都核对应用、租户、点击者、会话和原卡片，沿用原确认记录、执行权认领和回读核对。卡片功能关闭或投递失败时展示文字确认。验收应覆盖普通聊天、手机点击、取消、重复点击、过期预览和跨实例执行。

标题搜索和内容读取都走用户本人授权。标题搜索、按名打开的原生云文档、在线表格、多维表格和云盘下载分别存储授权，继续复用现有 `0022_work_integration_state` 表。原生云文档按名读取仍申请 `docx:document`，独立保存以保留日历授权。按标题搜索使用任务协议 4，文件读取使用协议 3。所有 Gateway 升级后再启用相应开关，确保授权回调可以落到任一实例。

在 UAT 私聊可用「读取我云文档空间下的 AA标准测试.docx」。完整结果中只有一个同名文件时直接进入内容读取，否则通过候选选择定位。候选和待处理授权链接跟随当前任务，换题或过期后需重新发起请求。搜索最多读取三页标题匹配结果，按名读取多维表格默认预览最多三张数据表。

旧版接口的 `docs_types` 请求筛选值 `doc` 涵盖新旧原生云文档。内容读取按 `docs_entities[].docs_type` 实际返回类型识别原生新版云文档 `docx`、在线表格 `sheet`、多维表格 `bitable` 和云盘文件 `file`。上传的 DOCX、XLSX 和 PDF 属于 `file`。仅返回 `doc` 的结果无法确认是新版云文档，文件夹也不在该搜索接口支持的类型内。遇到这两类情况，可请用户提供受支持文件的直接链接。[海能 Work 搜索云文档](https://open.w.cnooc.com.cn/document/ukTMukTMukTM/ugDM4UjL4ADO14COwgTN)

验收应分别确认搜索权限和内容权限，覆盖重名选择、无结果、跨账号权限、原生新版云文档的返回类型、工作表范围、多维表格、上传的 DOCX 与 XLSX、文本 PDF、扫描 PDF 和无下载权限。扫描 PDF 依赖现有企业解析服务。

## 定时任务推送到个人 Work 私聊

在使用共享 PostgreSQL 的 Gateway 上设置 `DEER_FLOW_WORK_SCHEDULED_NOTIFICATIONS_ENABLED=true`。全部 Gateway 升级后再开启该开关。通知采用当前 Work 应用身份调用发送消息 API，请核对机器人的发送权限及可用范围。页面可选目标只来自任务所有者当前已验证的 Work 私聊。历史连接可在原私聊里发一条消息补充验证。

通知按任务启用，成功的计划触发和手动运行都可投递。既有群机器人 Webhook 仍按任务提示词执行。投递结果独立显示在运行历史，原任务结果可以从平台打开。生产结果链接默认使用正式平台域名，UAT 可通过 `DEER_FLOW_WORK_SCHEDULED_NOTIFICATION_WEB_URL` 提供可访问的平台入口。

待投递记录复用 `0022_work_integration_state` 已建立的 `work_integration_records` 表。多个 Gateway 共用认领和去重记录，解绑、切换目标或撤销设置后跳过旧通知。结果不明的发送保留待核查状态。验收应覆盖成功运行、重复完成、跨实例接管、解绑和失败状态。

## 发布验收清单

1. 确认正式云文档主机、App ID、用户身份权限和已发布状态。
2. ITC 配置完整回调及服务器固定出口IP；两者是不同要求。
3. 备份并演练迁移；所有实例共享数据库、runtime-config 和稳定密钥，代理各层屏蔽 query。
4. 已有Work接入升级按[维护窗口说明](WORK_RELEASE_PREFLIGHT_20261104.md)停旧进程后更新；不能以`WORK_ENABLED=false`代替暂停，否则已启用渠道可能失去租约保护而多开连接。首次尚未配置渠道时也应先核对实际启动条件。
5. A/B账号分别授权；B不能读取A的私有文档、确认A的操作或使用A的浏览器授权结果。
6. 测试回调落不同实例、重启后确认、重复确认、解绑/撤权、Secret更新、断网和滚动更新。
7. 实测文档读/总结、受限编辑、整日查询、日程新建/编辑；修改必须有本人独立确认和回执。
8. 任何不确定写入先查 Work 结果，不能为了“补一条成功记录”重新执行。

涉及 Gateway、现有数据库及 Nginx；共享平台库的 PPT Runner 需上述迁移兼容镜像，
不是 PPT 业务重构。不需要为本功能单独重建 Sandbox、SkillHub 或 WeKnora。
实际发布需匹配生产 CPU 架构；不要使用本地 AMD64 测试镜像替代 ARM64 生产镜像。
