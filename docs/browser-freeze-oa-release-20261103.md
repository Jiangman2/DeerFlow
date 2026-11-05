# 浏览器导航冻结与 OA 60 分钟配置合并发布

## browser_navigate

统一工具装配入口默认不注册 `browser_navigate`，也过滤 MCP 的
`browser_browser_navigate`、`playwright_browser_navigate` 等服务前缀名称。
配置名命中时不导入该工具；最终工具真实名称命中时同样过滤，覆盖别名和 MCP 缓存。
过滤发生在延迟工具搜索目录装配之前，工具搜索无法重新发现被过滤的导航工具。
其他 browser/web 工具保留原策略。这不是禁用整个 MCP 服务、网络权限或直接 MCP API。
已经创建并正在执行的 Agent 不会被此源码补丁追溯修改。

发布环境显式设 `DEERFLOW_BROWSER_NAVIGATE_ENABLED=false`。
后续联通验证通过后，可由运维设为 `true` 恢复新建 Agent 的注册。
不用前端按钮或系统提示词假装冻结工具。

## OA 分支的实际改动

核对分支 `codex/oa-timeout-60m-pause-20261103`，固定提交
`7b8e0634fe96acc4bd58d06f3250c1cd09c0945c`，相对 `5a29e5aa6` 共 5 个文件。
内容是已执行脚本、测试、暂停状态记录、说明和字节保留规则，未更改应用默认值。
应用默认仍为 1800 秒；Deployment 的 gateway 容器通过
`OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS=3600` 覆盖到 60 分钟。
该变量在服务启动时加载，不会通过 exec 新开 shell 改变既有服务进程。
它不是大模型请求超时；已有 180 秒模型请求等待保持不变。

分支记录当时 Deployment generation 405、paused=true，70 个副本中 updated=3。
这是历史观测，不是本次对生产状态的新核验；旧进程和新进程当时可能混用不同 OA 超时。
不得重跑历史脚本或删除其执行标记。

本候选按字节保留该分支的 5 个变更文件，同时提供
`deploy/enterprise/browser-oa-performance-release-20261103.json` 作为本次发布的必需配置。
这是发布要求文件，不是可直接 kubectl apply 的 Kubernetes manifest。
镜像准备、安装验证和预加载完成后，最终发布补丁须一次包含新镜像、OA 3600、
导航冻结及性能日志开关，并明确处理 Deployment 暂停状态。源码阶段不恢复滚动。
最终回读新 Pod 环境与运行验收后，才可以声称统一生效。

本候选继续保留上一轮 265 项通过的内存优化与模型性能埋点。Frontend 变更仍需独立构建验收。
