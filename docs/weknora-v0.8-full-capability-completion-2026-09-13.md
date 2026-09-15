# WeKnora v0.8 全能力补齐与生产启用清单

更新时间：2026-09-15

目标版本：WeKnora v0.8.0，commit `1edcd54b43606d9079bb36650efe3f68707a79ea`

## 1. 当前生产基线

| 层 | 当前状态 | 结论 |
| --- | --- | --- |
| PostgreSQL | `schema_migrations = 90|false` | v0.8 数据迁移已完成且不是 dirty 状态 |
| WeKnora App | `0.8.0-enterprise-proxy1-anydoc1-arm64-20260912` | v0.8 服务端与 AnyDoc 已上线，企业代理补丁保留 |
| WeKnora UI | `0.8.0-enterprise-sso2-r5` | v0.8 UI 已上线，SSO 与嵌入式 DOM 防护保留 |
| DeerFlow Gateway | `20260914-enterprise-arm64-rc15-digitalemployee-promptfix1` | 10 个知识库工具、身份适配、数字员工、Browser Live 与提示词 ABI 修复均已进入生产镜像 |
| Nginx | `deer-flow-nginx-config-weknora-v08-ui-r5` | 同源嵌入与路由已切换，不能回退为固定 ClusterIP |
| AnyDoc | 已进入生产 App 镜像 | 13 种格式金样与正式切换已通过；既有 XLSX 路由冻结为 `builtin`，未自动重解析历史文档 |
| DocReader | 仍为 2026-07 固定镜像及 XLSX/Web 兼容层 | 必须单独升级并回放企业兼容补丁，不能因 AnyDoc 上线而删除 |
| WeKnora Sandbox | 路由、数据库和 UI 已随 v0.8 编译/迁移，但没有可用后端 | 待 ARM64 安全运行时；禁止直接挂载宿主机 `docker.sock` |

## 2. 官方 v0.8 能力状态矩阵

状态定义：

- **已上线**：生产 App/UI/数据库已包含并可继续做业务验收。
- **待专项验收**：代码和表结构已存在，但需要真实样本或特定配置验证。
- **依赖 Sandbox**：没有会话沙箱时入口可以存在，但执行链路不完整。
- **外部配置项**：原生适配器已经存在，是否可用取决于对方地址、凭据和网络，不应伪造默认配置。
- **有意不启用**：与现有企业边界冲突或不是服务端运行能力。

| 官方 v0.8 能力 | 当前状态 | 完成动作 |
| --- | --- | --- |
| v0.8 App/API 与迁移 071–090 | 已上线 | 保持 `AUTO_MIGRATE=false`、`AUTO_RECOVER_DIRTY=false` |
| v0.8 UI | 已上线 | 桌面端、移动端、管理员和普通用户继续回归 |
| 组织/共享空间/Agent/IM/Embed/API/MCP/Web Search/向量库/存储管理路由 | 已上线 | 用 `/api/v1/system/capabilities` 和角色账号逐项验收入口与权限 |
| 跨会话长期记忆 | 待专项验收 | 验证开关、显式记忆、推断确认/拒绝、检索、导出与权限隔离 |
| 消息用量、Artifacts、上下文压缩、提示词缓存标记 | 待专项验收 | 普通聊天可验 usage/compaction；Artifacts 的完整验收依赖 Sandbox |
| 知识库自动标签 | 待专项验收 | 使用测试知识库和现有模型验证，不修改历史标签 |
| XMind 解析 | 待专项验收 | 增加真实 `.xmind` 金样，不重解析历史文档 |
| AnyDoc 0.1.9 | 已上线 | 保持既有 XLSX 兼容冻结；新文档或用户主动重新解析可按知识库当前规则使用 AnyDoc |
| v0.8 DocReader | 未升级 | 固定 v0.8 源码构建 ARM64 镜像，移植 XLSX/Web 兼容层并做双路对比 |
| Docker/E2B/Cube Sandbox | 未启用 | 选择适配 ARM64 集群的安全后端并完成隔离、网络和会话持久性验收 |
| 租户 Skill Catalog、安装转录、Skill 环境变量 | 依赖 Sandbox | Sandbox 通过后再验 zip、GitHub/GitLab、ClawHub/skills.sh、停止/重装/卸载 |
| GitLab 与 Tencent IMA 数据源 | 外部配置项 | 保留原生入口；获得地址/令牌后分别联调增量同步和删除语义 |
| LiteLLM 模型提供方 | 外部配置项 | 保留原生入口；没有 LiteLLM 网关时不创建无效实例 |
| Exa 与 Metaso 搜索 | 外部配置项 | 保留原生入口；有凭据时再配置，密钥只进 Secret |
| OIDC JWKS | 有意不启用 | 当前 DeerFlow SSO 是唯一浏览器身份入口；除非企业 IAM 决定改用 OIDC |
| 复杂密码策略 | 有意不启用 | SSO 用户不依赖自助密码；启用前需评估既有本地账户 |
| DeepSeek Harness npm 插件 | 非服务端必需项 | 它是外部客户端插件；现有 DeerFlow 10 工具链不需要安装它 |

## 3. 剩余发布阶段

### 阶段 A：AnyDoc 候选、金样与生产切换（已完成）

完成结果：固定 AnyDoc 0.1.9 的 ARM64 App 候选已在三节点导入，13
种格式金样验证通过，生产 App 已切换。切换前为 174 个缺少显式 XLSX
规则的既有知识库补入 `builtin` 兼容规则，保护 857 个历史 XLSX 文档的
默认路由；没有启动历史文档重解析。此后新文档或用户主动触发的重新解析
遵循该知识库当前的显式规则，管理员可以在评估后将目标知识库改为 AnyDoc。

### 阶段 B：DocReader v0.8

1. 从同一个固定 v0.8 commit 构建 DocReader ARM64 镜像。
2. 回放当前 XLSX/Web 兼容 ConfigMap，并验证 gRPC health、`Read`/`ReadStream`、图片输出目录。
3. 用与阶段 A 相同的样本做路由与回退测试。
4. 以同一新 DocReader 镜像为基础重建 `deer-flow-mineru-adapter`，回归 Word、可搜索/扫描 PDF、PPT/PPTX 和 Office 预览链路。
5. 分别切换 `weknora-docreader` 与 MinerU 适配器，不改知识库和解析规则；任一回归失败可独立回滚。

### 阶段 C：Sandbox 基础设施

当前只读采集包：

- `weknora-v08-sandbox-readiness-20260913-r1.tar.gz`
- SHA-256：`fd17b9d1ec91fc0aaeef114bf7fbccb1423d9953c5b7892c96930b0a5df6cfe5`
- 该包只采集基础设施与 v0.8 数据结构证据，不创建 Kubernetes 资源。

1. 先运行只读基础设施预检：节点架构、RuntimeClass、CNI/NetworkPolicy、存储、资源、Kubernetes RBAC 和镜像供应链。
2. 不采用向 WeKnora App 挂载 `docker.sock` 的生产方案。
3. 官方 Agent-Sandbox 控制面与模板必须重新构建为 ARM64；其模板中的 `envd` 也必须是 ARM64，并固定镜像摘要。
4. 在独立命名空间部署最小权限控制面；System Token 由 Secret 生成，禁止沿用示例默认值。
5. 默认拒绝外联，只按企业白名单放行；沙箱 Pod 以非 root、只读根文件系统、资源限额和 seccomp 运行。
6. 完成创建、命令、文件、附件、Artifacts、TTL、暂停/恢复、删除、并发和跨 App 副本会话绑定测试。

### 阶段 D：依赖 Sandbox 的 v0.8 原生能力

1. 创建一个受控测试 workspace 的 E2B 兼容 Sandbox 配置。
2. 验证系统管理员与普通 workspace owner 的原生 RBAC。
3. 验证 Skill Catalog、安装转录、个人/空间环境变量不可回读、Agent shell/file 工具和生成文件。
4. 确认失败安装、取消、超时和清理不会遗留 Pod、凭据或可访问文件。
5. 验收通过后再逐 workspace 开放，不一次性给全部生产用户启用。

### 阶段 E：全能力回归和收口

1. 运行 `/api/v1/system/capabilities`，确认各能力项与实际配置一致；未配置的外部提供方以及 Docker Sandbox 可以保持 `supported=false`，但对应原生配置入口和权限边界必须存在。
2. 用系统管理员、workspace owner、可编辑成员和只读成员四类账号验收页面入口与后端拒绝边界。
3. 回归 DeerFlow SSO、10 个 WeKnora 工具、知识库分享、历史文档检索、模型代理、移动端和退出隐藏策略。
4. 检查所有生产 Deployment 的镜像摘要、数据库 `90|false`、队列、错误率和回滚材料。
5. 清理阶段性探针资源；保留带 SHA-256 的证据归档和迁移前回滚点，直到稳定观察期结束。

## 4. 不变的企业边界

- 不重建或覆盖现有知识库、用户、workspace、共享关系和历史文档。
- 不因启用 AnyDoc 自动重解析历史文档。
- 不删除 MinerU、DocReader 或现有显式解析规则。
- 不绕过 DeerFlow SSO、WeKnora 原生 RBAC 或 Gateway 的用户范围校验。
- 不把模型、外部数据源、搜索或 Sandbox 凭据写入镜像、文档或 ConfigMap。
- 不把 `docker.sock` 暴露给 WeKnora App。
- 不把外部提供方“有入口”误报为“已联通”；没有凭据和真实网络验收只能记为可配置。
