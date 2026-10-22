# 海能 Work 生产候选交接清单

2026-10-22。本分支只交付源码、测试及发布配方，不部署服务器，不修改正式分支，
不代表正式 Work 权限、数据库版本或 ARM64 镜像已经验收。
详细机制见 [WORK_PRODUCTION.md](WORK_PRODUCTION.md)。

## 代码上传方

- 提交完整 Work 模块、渠道/回调/认证/日志改动、ORM 注册及 0020 迁移、代理配置、
  生产配置默认值、测试及本组文档。新增文件不能只靠 `git commit -am` 收集。
- 保留受环境开关控制的 UAT 支持；不上传真实凭证、token、状态密钥、数据库导出、
  用户文件、本机运行配置、Windows 模型桥、10086 中继或本地测试镜像。
- 向部署同事提供本分支提交 SHA。分支基于已联调的本地源码；合并前对照正式分支
  后续修复和当前数据库迁移链，不以整目录覆盖、不强推、不擅自发布版本标签。

## 部署同事

### 合并及镜像

1. 核对实际正式分支/数据库版本，避免迁移分叉或同号冲突。候选链为
   `0019_digital_employee_agents -> 0020_work_integration_state`，
   新增 `work_integration_records` 和两个索引，不修改现有绑定表。
2. 构建匹配生产 CPU 架构的 Gateway；按锁文件安装依赖，确认包含新模块、回调路由、
   ORM 与迁移，不能直接用本地 AMD64 镜像替代 ARM64 发布。
3. 共享平台库的专用 PPT Runner 同样要更新。使用
   `docker/ppt-master-runner/Dockerfile.production-work-overlay`，先核验其基底为
   `RUNNER-LOCK.json` 的 R3 current_image/current_manifest_digest；通过
   `RUNNER_BASE_IMAGE` 传入核验后的不可变基底引用。保留专用 Agent 和入口，
   仅叠加 Work ORM、注册与迁移；分别验收空库建表和 0019 升级。
4. 新 Runner 使用新标签，并记录实际 digest/架构/测试回执；不要修改旧 lock 来伪造
   新构建验收，也不要把旧镜像名当成新产物。对其他共用数据库且启动时迁移的进程同样检查。
5. 应用所用 Nginx 配置。仓库 enterprise 清单仍包含历史镜像标签，必须按线上现状
   定向合并和核对差异，不整份原样覆盖现有部署。不要求更新前端、Sandbox、SkillHub 或 WeKnora。

### 配置

| 配置 | 正式部署要求 |
| --- | --- |
| `DEER_FLOW_WORK_PROFILE` | `production` |
| `DEER_FLOW_WORK_ENABLED` | 先明确 `false`；配置齐全后灰度启用 `true` |
| `DEER_FLOW_WORK_DOCS_UAT_ENABLED` | `false`，不带入遗留 UAT 开关 |
| `DEER_FLOW_WORK_STATE_KEY` | 独立的 32 随机字节 URL-safe Base64，通过部署 Secret 注入；各实例相同且重启保留 |
| `DEER_FLOW_WORK_PRODUCTION_CALLBACK_URL` | `https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback` |
| `DEER_FLOW_WORK_PRODUCTION_DOCUMENT_HOST` | `hnwork.w.cnooc.com.cn` |
| `DEER_FLOW_WORK_PRODUCTION_API_URL` | 默认 `https://open.w.cnooc.com.cn` |
| `DEER_FLOW_WORK_PRODUCTION_AUTHORIZE_URL` | 默认 `https://accounts.w.cnooc.com.cn/open-apis/authen/v1/authorize` |
| `DEER_FLOW_WORK_ALLOWED_OWNER_IDS` | 可选的新能力灰度名单，不限制渠道绑定/普通聊天；全量开放时显式为空，无需枚举所有用户 |
| `DEER_FLOW_WORK_DOCS_MODEL` | 可选，准确匹配批准模型名；不填使用平台默认模型 |
| `DEER_FLOW_WORK_MAX_JOBS` / `DEER_FLOW_WORK_PER_USER_JOBS` / `DEER_FLOW_WORK_CONCURRENCY` | 默认 32 / 4 / 2，执行并发为每进程值 |

全量开放也可同时移除 `DEER_FLOW_WORK_ALLOWED_OWNER_IDS` 和兼容变量
`DEER_FLOW_WORK_DOCS_ALLOWED_OWNER_IDS`；只删前者可能继承旧测试名单。
空名单不放宽当前绑定、本人 OAuth、原资源 ACL 和写入确认。

管理员网页保存的 App ID/Secret 优先于环境引导值。若线上曾填写 UAT 凭证，
必须在原渠道设置中改成正式凭证；只改 `DEER_FLOW_WORK_PRODUCTION_APP_ID/APP_SECRET`
不会覆盖已存配置。正式凭证由 ITC 安全提供，不从本地 UAT 复制。切换应用后按真实
Work 身份复核已有绑定，必要时用户重新绑定/授权，不能用旧应用 token 兜底。

所有 Gateway 共用 PostgreSQL、稳定状态密钥及原渠道 runtime-config 存储/锁。
不部署本机中继、`DEER_FLOW_WORK_DOCS_RELAY_KEY`、10086 转发或 Windows 模型桥配置。

### 入口、网络、发布及回退

- 备份数据库和渠道配置，演练迁移。正常启动路径执行 Alembic，不手改版本号跳过建表。
- 提供正式 HTTPS 和证书；将 `/api/integrations/work/oauth/authorize`、
  `/api/integrations/work/oauth/callback` 两条精确路径转到 Gateway，不能强制跳平台 SSO。
  不公开整个 integrations 前缀，不关闭全站认证。
- Nginx、外层入口/WAF/APM 均屏蔽 OAuth query，不能记录 code/state/ticket。
- 验证 Work API 和 SDK 实际 WSS 目标、正式模型网络及批准模型凭证。把正式服务器
  真实、稳定的来源地址交 ITC；不能用开发电脑的临时来源 IP。
- 先关闭新增功能部署并确认所有共享库消费者兼容，再灰度启用。首轮建议单接收实例；
  停止其他未参与同一 SQL 租约、却仍连接同一正式应用的旧/测试服务，避免抢消息。
- 实测重启、滚动更新和密钥/凭证配置行为。回退镜像也必须认识 0020；保留数据库与密钥，
  不把删除 Work 表、强行 downgrade 或恢复整库当成普通功能回退方案。

## ITC

1. 确认正式租户、正式 App ID/Secret、机器人能力、首批可用人员及发布状态。
   UAT 权限不会自动继承；凭证通过安全渠道交平台管理员，不进 GitHub/普通群聊。
2. 精确登记 OAuth 回调：

   ```text
   https://cnoocclaw.cnooc.com.cn/api/integrations/work/oauth/callback
   ```

   这是用户授权回调，不是消息事件地址，也不是 localhost/10086。ITC 不需另写程序或 Skill。
3. 开通并发布以下**用户身份**权限，名称以海能 Work 实际目录核对：

   | scope | 本轮用途 |
   | --- | --- |
   | `docx:document` | 读取/总结及已实现的受限新建/编辑 Docx |
   | `calendar:calendar:readonly` | 日程查询、主日历定位、参会人核对及写后回读 |
   | `calendar:calendar` | 新建、编辑个人日程 |

   当前日历授权包两项都请求，不以写权限推断已有只读权限。确认授权用户信息接口能返回
   `open_id`，用于与原消息发送者核验；不为此额外申请手机号/邮箱权限。
4. 保持并核验原机器人接收私聊、回复消息、发送/更新交互卡片及状态表情权限。
   使用长连接订阅 `im.message.receive_v1`，不要另加公网消息 webhook 或把 OAuth
   回调当成消息地址。具体消息 scope 按实际控制台关联 API 核验，不套用其他版本名称。
5. 将同事提供的正式固定来源 IP/准确出口池加入应用 API 白名单；配置与权限发布生效后
   给出确认。用户仍需逐人 OAuth，云文档/日历自身 ACL 不会因应用开通权限而互相开放。

## 联合验收与能力边界

- 普通消息正常收发，正式文档链接触发 OAuth 并返回总结；无需本机回调服务。
- 查询指定日期完整日程，新建/编辑先预览，再由原发送者独立确认并回读。
- A/B 两个正式测试账号：不能串用授权链接、token 或确认码；B 不能凭 A 的权限访问
  A 独享且未共享给 B 的文档。
- 检查重复确认、过期、撤销/解绑、跨实例回调、重启及滚动更新；不确定写入先去 Work
  核查，不自动重试。token 到期重新个人授权，不要求反复解绑。
- 删除、分享、邀请和会议室操作仍未开放。日程编辑仅限本人组织、无其他参会人/资源、
  非周期/非全天的个人日程；文档仅限已实现的受限操作，不宣传任意富文本覆盖。
- 专项测试不是完整 CI 或生产验收；完整套件的既有失败必须单独归因，并在正式依赖和
  隔离 PostgreSQL 上复测。新 Runner 的实际 ARM64 构建及上线回执仍由部署同事完成。
