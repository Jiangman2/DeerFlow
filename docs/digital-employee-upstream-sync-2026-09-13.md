# 数字员工相关 DeerFlow 上游同步说明（2026-09-13）

## 对比基线

- 当前企业分支提交：`af6c9e7642393cbbc0afc5f861c0b8e1656ef2e7`
- 与官方主线共同基线：`94a34f382d6672342998b09dda893cdc79d6dec7`
- 本次审计的官方主线：`98ae762db98c3002ad1b1d8e4e0ae75ce93cbce2`（2026-09-13）

本次继续采用“按能力移植”，不整段覆盖企业分支。现有的用户隔离、数字员工广场、团队发布、SkillHub、移动 WebView、文件安全与企业登录边界均被保留。

## 已同步的官方能力

- `0d4d0cb17`：可配置的数据库 Agent 存储、SQL 仓储与一次性文件导入脚本。
- `20debf9cc`：每个数字员工独立模型、`temperature`、`max_tokens`、思考开关和 `reasoning_effort`。官方当前没有开放 `top_p`，本分支同样以明确白名单拒绝未支持的模型参数。
- `fa496c0c8` / `e5c62cab5` / `8be08101b`：Browser 自动化基础能力、数字员工 Browser Live 面板与 JPEG 实时帧。
- `791012692`：SQL 存储签名包含实际配置内容，修改后可正确刷新缓存。
- `fb722770e`：显式配置缺失或格式错误时失败关闭，不静默回退到其他存储。
- `ac2b6415e`：Unicode 展示名。稳定内部名称仍保持安全字符集，展示名可使用中文并拒绝控制/不可见字符。
- `c35022e18`：工具组为空时仍正确显示 Skill 能力标签。
- `822c7bca4`：删除数字员工前后取消尚未完成的该员工记忆提取任务。
- `3bccd1474`：嵌入式 Agent 客户端缓存按有效用户隔离。
- `7df44f586`：拒绝用空白内容覆盖现有 `SOUL.md`。
- `8ee3c8350`：后台任务引用保留，避免进程内长任务被提前回收。

## 企业分支增强

- 个人数字员工记录严格按 `user_id` 隔离；团队发布副本仍遵循原有审批与广场授权。
- Browser Live 使用同源、已鉴权的 Gateway HTTP/WebSocket 路由，校验线程归属和 WebSocket Origin，并默认拦截私网地址以减少 SSRF 风险。浏览器原始地址不暴露给客户端。
- 自定义头像只接受 PNG/JPEG/WebP，上限 5 MB，校验像素、尺寸和静态帧后统一转换为 512×512 WebP。
- 分类定义仅管理员可增删改排序；个人用户可为自己的数字员工选分类，并手动或规则智能生成标签。被数字员工引用的分类禁止直接删除。

## 本版本仍保留的边界

- SQL 负责个人数字员工的配置、推理参数与 SOUL；卡通头像二进制文件、管理员分类文件、团队发布快照仍存放在现有共享持久卷。它们不是“未完成”，而是为了保持已有发布和文件服务协议。
- Browser 会话是 Gateway 进程内状态。当前 `k8s/local-pvc` 为 1 个 Gateway Pod、1 个 uvicorn worker，可安全使用。未来扩容到多 Pod/多 worker 时，必须先增加线程粘性路由或分布式 Browser Session Broker；启动检查会在不支持的多 worker 组合下失败关闭。

部署、迁移和回滚步骤见 [`digital-employee-upgrade-operations.md`](digital-employee-upgrade-operations.md)。
