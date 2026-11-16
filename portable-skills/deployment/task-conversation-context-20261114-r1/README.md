# 当前任务会话链接：Gateway 候选与发布准备

此目录包含候选源码、离线验证和未密封发布模板。另有新建的密封发布产物，尚未执行生产修改。

`bash_tool` 在每次 POSIX 命令执行前，从当前 Runtime 的 thread 身份和平台配置的站点计算会话 URL，通过 `DEERFLOW_CONVERSATION_URL` 提供给子进程。Skill 因而能在首次构建正文时就附上当前对话链接，无须等待 `present_files` 的工具回执、让模型转录 JSON，再运行一次拼接命令。

这提供的是会话定位信息。文件生成、平台发布、Webhook 发送仍是各自的真实边界；URL 存在不表示附件已经发布或消息已发送。Skill 应在四件文件成功发布后只发送已组合好的同一份正文。此平台候选本身不发送消息，也不保证第三方 Webhook 的 exactly-once。

## 最小改动

- 只修改 `deerflow/sandbox/tools.py`：加入通用会话上下文前缀，与原频道身份前缀组合。远程沙箱和本地 POSIX 共用相同逻辑，异步 Bash 调用仍复用原同步实现。
- thread 解析优先级与当前 `present_files` 一致：`runtime.context.thread_id` → `runtime.config.configurable.thread_id` → LangGraph 当前配置。已选中的非法 thread 不改用另一个 thread。
- 使用 Gateway 416 已锁定的 `current_conversation_url`；只接受平台配置的站点，不使用模型提供的 URL。没有有效身份或站点时，每次明确 `unset`，清除持久 Shell 中可能残留的旧值。
- URL 经 `shlex.quote` 后进入命令前缀。`execute_command(env=...)`、已有 Skill Secret、GitHub token、频道用户 ID、超时与输出过滤保持原逻辑，避免触发另一条 AIO `bash.exec` 传输路径。
- 本地 Windows 分支保持原行为，不插入 POSIX `export/unset`。本候选为远程 Linux/本地 POSIX 提供这一环境变量，不宣称 Windows 本地也有该能力。
- 不改 `present_files`、消息发送器、Skill 恢复契约、数据库、凭据和部署策略。

## 来源边界

`SOURCE.json` 保留父文件和候选文件的 SHA256。`parent/` 是原 `tools.py` 的字节级副本；`tools.patch` 是唯一平台业务源码差异。

父 `tools.py` 来源是 `.codex-post44-sync-20261113`，SHA256：

```text
a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c
```

初始 Gateway 416 的 13 文件补验记录不包含这份 `tools.py`，所以 `SOURCE.json` 保留当时“生产父源码未验证”的事实。随后收到 `log(25).txt` 的新只读核查，三份固定源码均与上述来源一致；独立 `FIELD-BASELINE.json` 记录日志摘要、SHA256、Gateway 416 完整 spec SHA 与一个稳定样本 Pod，未改写原始 SOURCE 标记，也不将该样本扩大成全体 Pod 验证。发布工具仍需现场重新校验父版本和配置，才能执行唯一一次 CAS patch。

`conversation_url.py` 从 `portable-skills/deployment/gateway-link-r3` 原样复制，对应 Gateway 416 已锁定 SHA256：

```text
f5ac36f16038a3532cd3110d134b72e58c42693c1b9ab9a1b6390d9b4209c0b8
```

这个 helper 未修改，部署时也无须因本候选重新替换它。根 `backend`、历史快照和历史发布记录均未修改。

## 离线验证

使用现有完整 backend 虚拟环境，从历史依赖目录加载**完整候选模块**，并加载精确 Gateway 416 helper；没有 AST 函数抽取或将工具装饰器替换成空实现。测试只模拟沙箱传输和平台配置，不启动应用服务、不连接生产。

```powershell
& 'C:/Users/15285/Documents/deer-flow/deer-flow/backend/.venv/Scripts/python.exe' -I -B 'portable-skills/deployment/task-conversation-context-20261114-r1/run_tests.py'
```

覆盖当前任务身份与三种来源优先级、无身份/非法 thread/非法站点、忽略模型 URL 和继承环境值、真实 POSIX Shell 的 stale 值清除与引用转义、远程/本地两条 POSIX 路径、Windows 本地不改行为、同步/异步共享实现、现有凭据与频道身份组合以及发布前可读取 URL。另外通过真实 POSIX Shell → Python 子进程 → Skill 2.1.2 `host_conversation_context()` 检查 UUID、平台合法非 UUID 和无身份三种情况；不修改 Skill 文件、不调用其采集器。结果保存在 `VALIDATION.json` 和 `TEST-RESULTS.xml`。

这项离线结果不代表生产业务验收；真实对话一次交付仍待核验。

## 发布模板与可恢复验收

`activate-task-conversation-context-20261114-r1.py` 的两个嵌入资源仍为空，直接执行会拒绝；`seal_task_context.py --reviewed` 在新的 `release/task-conversation-context-20261114-r1` 目录生成独立密封产物。密封文件不依赖本机仓库路径。

范围是 Gateway 416 → 417：新增一个只有 `tools.py` 的 immutable ConfigMap、一个 volume 和一个只读 subPath mount。JSON patch 先验证 UID、resourceVersion、generation 和完整原 spec，然后仅追加这两项。镜像、CA Secret 引用、所有既有 mount/volume/initContainer、策略、资源、环境变量和预算均原样保留。预算配置只读绑定已验 r5 `PLAN.json`，不重建或修改预算 ConfigMap。现有五个配置 ConfigMap（包括 extensions）在切换前后保持完整 identity。

工具沿用已验 r5 的 Pod admission、稀疏列表、70 个 Pod 的源码与 Ready/运行身份、Service/EndpointSlice 精确闭合检查；每个 Pod 核验 14 份源码/配置文件。实际 Gateway Python 编译完整候选，并在隔离命名空间里用固定源码函数和合成任务身份验证前缀；不导入应用、不真正执行 Bash、不发送任何业务消息。回执明确这与实际业务验收的区别。

滚动进度同时显示 generation 与 observed generation。只有控制器观察到 417、70 个新副本全部就绪，并且源码/端点核验全部通过，才产生 `TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED`。

- `--activate-task-context-once`：唯一一次创建/切换；通过后保存根用户 0600 的 `RUNTIME-ACCEPTANCE.json`。
- `--verify-readonly`：只读核对现有日志和线上状态，不重放切换、不写回执；仍在滚动时直接返回进度。
- `--verify-and-save-receipt`：同样只读核对集群，通过后仅在本机私有发布目录保存 immutable `RUNTIME-ACCEPTANCE.json`，用于终端意外关闭后的后续 Skill 发布绑定；不会重放切换或修改工作负载。已有匹配回执只读取，不覆盖。

发布目录为 `/var/lib/portal-task-conversation-context-20261114-r1`。不要删除一次性标记、覆盖旧回执或重复激活。

`OPERATOR-VALIDATION.json` 记录 40 项边界/恢复/Pod admission/端点检查。`PYTHON37-VALIDATION.json` 是实际无网络 Python 3.7.17 的密封资源加载、三个 CLI 错误主机零命令保护和两类篡改资源拒绝结果。源 tools.py 含新 Python 语法，本机 3.7 只解码和校验，不编译它；完整源码编译在 Gateway Python 执行。
