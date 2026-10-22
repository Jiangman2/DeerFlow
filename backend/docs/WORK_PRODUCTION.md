# 海能 Work 生产接入与本地 UAT 开发

## 本次边界

新增生产配置、Gateway 浏览器回调、共享加密状态和迁移 `0020_work_integration_state`。
**代码候选，不代表已发布、已获得生产权限或已通过真实生产机器人验收。**
功能仍默认关闭。默认 profile 是 `production`；UAT 必须显式选择。
文档删除/分享、日程删除、参会邀请和会议室操作仍未开放，不因 broad scope 授权而解禁。
分工、发布顺序及验收门槛见 [WORK_RELEASE_CHECKLIST.md](WORK_RELEASE_CHECKLIST.md)。

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
挂载同一份可用存储。异步接收器每 5 秒核对文件变更，不是即时跨集群配置广播。

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
| `DEER_FLOW_WORK_ALLOWED_OWNER_IDS` | 可选的文档/日历能力灰度名单，不限制渠道连接或普通聊天；全量开放时设为空，仍须有效绑定和本人授权 |
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
发布前备份数据库并演练从 0019 升级。多实例生产必须使用共享 PostgreSQL；SQLite
仅用于单节点开发/离线测试，不承诺跨节点文件锁可靠性。

所有共享此平台库且执行 Alembic bootstrap 的服务都必须认识 0020，包含专用 PPT Runner。
正式 Runner 可使用 `docker/ppt-master-runner/Dockerfile.production-work-overlay`，
以 `RUNNER-LOCK.json` 中已验收的 R3 current_image/current_manifest_digest 为经核验基底，
只补入标准迁移、Work ORM 和注册入口，保留原 Runner 应用/Agent。ORM 也必须包含，
否则空库可能在未创建 Work 表时被旧模型集合直接 stamp 到新 head。
产物必须使用新不可变标签，重新记录实际架构、digest 和验收结果；旧 R3 lock 不是新镜像回执。
不必把 `production-source` 中的整个历史 harness 复制覆盖到当前 Gateway。

AES-256-GCM 加密状态，并把命名空间、记录种类、随机标识及主体身份绑定到认证数据。
token 不进入模型、Skill、Sandbox、浏览器、普通日志或配置文件；只进入加密数据库记录。
OAuth job/state、确认预览、去重标记、少量定位上下文共享保存；绑定每次通过现有绑定仓库核验。
容量在 SQL 事务内串行判定，已完成任务不占活跃配额，单个用户不能占满整个队列。
执行并发2是每进程值，不应解释为全平台最多2个模型请求。

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

## 发布验收清单

1. 确认正式云文档主机、App ID、用户身份权限和已发布状态。
2. ITC 配置完整回调及服务器固定出口IP；两者是不同要求。
3. 备份并演练迁移；所有实例共享数据库、runtime-config 和稳定密钥，代理各层屏蔽 query。
4. 先关闭功能部署源码/镜像/路由，再用白名单小范围启用。
5. A/B账号分别授权；B不能读取A的私有文档、确认A的操作或使用A的浏览器授权结果。
6. 测试回调落不同实例、重启后确认、重复确认、解绑/撤权、Secret更新、断网和滚动更新。
7. 实测文档读/总结、受限编辑、整日查询、日程新建/编辑；修改必须有本人独立确认和回执。
8. 任何不确定写入先查 Work 结果，不能为了“补一条成功记录”重新执行。

涉及 Gateway、现有数据库及 Nginx；共享平台库的 PPT Runner 需上述迁移兼容镜像，
不是 PPT 业务重构。不需要为本功能单独重建 Sandbox、SkillHub 或 WeKnora。
实际发布需匹配生产 CPU 架构；不要使用本地 AMD64 测试镜像替代 ARM64 生产镜像。
