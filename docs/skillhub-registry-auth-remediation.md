# SkillHub 未授权访问整改

本次只修复渗透报告第 3 条。普通业务用户必须登录才能读取技能、标签、版本、文件及下载包；有效的原生 API token 仍可调用允许的接口。端口、上传和 API 文档配置不在本次改动范围内。

## 修复内容

- 企业路由策略将原生公开业务读取接口改为需要认证，保留原有角色权限、token 校验、scope 限制、CSRF 和登录初始化规则。
- 企业集成开启时，禁用原生高优先级公开标签/兼容注册过滤链。这些链会绕过普通路由策略，所以只修改 permitAll 路由清单不足以修复。
- Nginx 的 SkillHub 页面和浏览器 API 检查当前 DeerFlow 会话。页面匿名访问进入 `/login`，API 返回 401。SSE 和 bootstrap 的独立 location 也保留相应校验。
- 三份 Nginx 配置的新增 API 路由和两个内部鉴权路由均清除客户端 `Forwarded`、`X-Forwarded-Port`，覆盖 `X-Forwarded-Host` 并隐藏三个框架版本响应头，避免覆盖既有整改防护。
- bearer 请求只跳过 Nginx 的浏览器会话检查，由修复后的 SkillHub 独立验证 token、账户状态和权限。伪造 bearer 字符串不能获得业务数据。
- 工作区鉴权服务不可用时，仅显示恢复界面，不渲染工作区菜单或业务 children；恢复有效身份后重新执行服务端布局鉴权。

## 生产构建输入

SkillHub 基于对应的审核源码构建，不能只重新使用旧镜像。将以下 overlay 放入匹配模块：

| overlay 路径 | 目标 |
| --- | --- |
| `k8s/skillhub/server-overlay/src/main/java/com/iflytek/skillhub/auth/` | `skillhub-auth/src/main/java/com/iflytek/skillhub/auth/` |
| `k8s/skillhub/server-overlay/src/main/java/com/iflytek/skillhub/integration/` | `skillhub-app/src/main/java/com/iflytek/skillhub/integration/` |
| `k8s/skillhub/server-overlay/src/main/java/com/iflytek/skillhub/compat/` | `skillhub-app/src/main/java/com/iflytek/skillhub/compat/` |

同时覆盖 auth/integration 对应测试目录，执行包含依赖模块的 Maven 测试。新增的 `compat/ClawHubRegistrySecurityConfig.java` 必须进入构建，不能遗漏。本地构建脚本 `local-dev/Build-SkillHub.ps1 -ServerOnly` 已包含这些步骤。

`local-dev/` 是此开发工作区的本地辅助目录，位于 DeerFlow Git 仓库之外，本次 GitHub 提交不包含它，也不包含本地凭证、运行日志或 SkillHub 上游源码。生产构建请使用已审核的对应 SkillHub 源码，按上表覆盖本仓库中的补丁，执行 Maven 编译，再用 `k8s/skillhub/Dockerfile.server-overlay` 将生成的 `skillhub-app.jar` 打包成新镜像。该 Dockerfile 需要先生成 JAR，单独执行 Docker 构建不会编译补丁。

生产保留 `SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED=true`，使用新不可变标签构建 ARM64 SkillHub server 镜像。共享身份 bridge Secret 和既有数据库/角色不需要修改。frontend 从本次源码构建并发布。SkillHub web 镜像无业务代码变更。

本地 AMD64 验证镜像不能直接作为生产 ARM64 镜像使用，也不能用源码变更代替镜像发布。

## 发布顺序

1. 备份当前 SkillHub server 镜像引用和生效 Nginx 配置，核对实际镜像/源码基线。
2. 先发布 SkillHub server，确认所有副本使用新镜像且企业集成已开启。
3. 从内部入口验证匿名和伪造 bearer 访问 `/api/web/labels`、`/api/web/skills` 返回 401，再更新 Nginx。**带 bearer 的入口检查依赖已修复的后端，不能单独先发布 Nginx。**
4. 将本次 `k8s/local-pvc/nginx.conf` 中的鉴权增量合入生产生效配置，保留现场 TLS、上游、其他业务路径和热更新差异；执行 `nginx -t`，再加载配置。
5. 发布 frontend，验证鉴权异常时不展示工作区，以及恢复后可以重新进入。
6. 从原扫描入口复测两个报告接口，并验证登录、原生会话、下载、API token 与角色权限。

如需回滚，仅恢复本次相关镜像和入口配置；不得修改用户角色、删除技能或清理业务数据库。

新增的两个内部鉴权 location 使用 `http://gateway:8001/api/v1/auth/me`，与既有 bootstrap 一样采用带路径的静态 Service 地址，以兼容生产配置渲染器。部署时核对该 Service 名称；不要改成会被启动脚本移除的 `$gateway_upstream`。本地预览的 Kubernetes port-forward 会随 Nginx Pod 重启断开，需用已有 `local-dev/Start-FrontendDev.ps1` 恢复。

## 本地验证方式

仅操作 `docker-desktop` context，脚本会检查三个节点均为 `desktop-*`。后端验证临时端口只绑定 `127.0.0.1:18088`。

复测时先启动 `kubectl --context docker-desktop -n deer-flow port-forward service/skillhub-server 18088:8080 --address 127.0.0.1`，验证结束后关闭该临时进程。

- `local-dev/Verify-SkillHubRegistryAuth.py --baseline` 记录修改前匿名访问结果。
- `local-dev/Verify-SkillHubRegistryAuth.py` 检查入口/直连后端匿名拒绝、伪造凭证、普通用户登录和签名 bootstrap、技能 ZIP 下载、原生会话、有效/撤销 token 及 scope 拒绝。
- 测试凭证仅从本地受保护文件读取，不写入输出；临时 API token 测试后撤销。
- `local-dev/Apply-SkillHubRegistryAuth.py` 仅向本地已有 Nginx 配置加入鉴权增量，先在实际 Pod 校验语法再发布，保存原配置供回滚。

## 验收标准

匿名、无效会话、伪造身份头和无效 token 不能得到业务数据。登录用户正常读取/下载；有效 token 无需浏览器 Cookie 可读取授权资源，超出 scope 的操作返回 403，撤销 token 返回 401。仅有旧 SkillHub SESSION、已失去 DeerFlow 会话的浏览器请求被入口拒绝。普通用户不能操作管理员接口。

本地结果保存在 `local-dev/runtime/registry-auth-verification/`。生产整改关闭结论须以部署后的现场复测为准。

## 2026-10-10 本地结果

- 修改前，报告两个接口在统一入口和后端直连均为匿名 HTTP 200，共 4 项基线检查。
- 新 SkillHub server 镜像 `deerflow.local/skillhub-server:local-20261010-registry-auth-1` 已在本地 Docker Desktop 集群部署，Nginx 增量通过实际 Pod 的语法校验并加载；本地 frontend 开发服务使用新源码。
- 29 项真实 HTTP 检查全部通过：报告接口匿名 401、伪造 Cookie/bearer/身份头/内部头 401、匿名页面进入登录页、匿名 SSE 401、登录与下载 200、有效 token 200、普通用户管理员接口及只读 token 越权 403、撤销 token 401、仅保留 SkillHub 会话的浏览器入口 401。
- 后端 50 项安全测试、前端 24 项相关测试通过；完整 TypeScript 检查及修改文件 ESLint 检查通过。
- 临时测试 token 已撤销，临时后端验证转发已关闭；既有本地预览仍可访问。未连接或修改生产服务器。

## 合并安全回归

`tests/test_skillhub_edge_security.py` 用本地真实 Nginx 和模拟上游检查三种入口配置：有效模拟 token 的转发头清理/版本头隐藏、浏览器及 API 鉴权子请求、匿名 API/SSE、仅原生 SESSION、无效 bearer 和登录跳转。模拟上游只验证入口行为，不代替原生后端 token、scope、RBAC 或生产验收。

设置 `NGINX_TEST_BINARY`，以及 Kubernetes TLS 测试所需的 `NGINX_TEST_CERT`、`NGINX_TEST_KEY`，运行：

```bash
python -m unittest discover -s tests -p test_skillhub_edge_security.py -v
```

合入 HTTP 安全整改分支后，还须运行 `test_http_edge_security.py`，确认两个生产域名、危险方法、Host 校验和既有路由没有回退。此源码补丁不更新生产配置，也不替代生产 ARM64 镜像构建与发布。
