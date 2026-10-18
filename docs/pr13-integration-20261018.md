# PR #13 与本次热更的源码核对

PR #13 的原始提交是 `8a150f2b1e85cff33dd8afb68c5cfa9fd070d9fd`，目标为
`codex/enterprise-consolidation-20260716`。本记录区分源码集成与生产发布；它不表示
已经合并到 0716、修改生产环境或完成数据库恢复。

维护者反馈曾把 PR 合入生产源码、运行失败后回退。目前没有当时的构建/启动命令和
首个错误，因此本轮修复已复现的缺陷，不把它们直接认定为该次事故的全部原因。

## 与已构建镜像的比较

按最终构建输入比较实际 Git blob，而非比较混有其他工作的共享目录：

- Gateway/Runner 正向及兼容覆盖层共 54 个产品文件与原 PR 完全相同。
- Host 的 12 个产品文件相同；`ppt_confirmation_proposal.py` 由共享
  `backend/packages/harness/deerflow/ppt_confirmation/proposal.py` 复制生成，
  不是漏提交的独立源文件。
- 最终 Frontend 的 667 个输入中，665 个相同，`next-env.d.ts` 是生成文件；
  唯一真正的产品内容差异是 demo artifact route 的 projected-volume 路径修复。
  该修复现已同步，并增加合法 `..timestamp` 投影路径和双点前缀文件名回归；
  `..` 父路径逃逸和越界 symlink 仍拒绝。

这不是“整个 PR 与镜像完全等价”的证明：镜像继承固定生产基础层，PR 还包含
WeKnora/MinerU 等其他源码与操作工具；这些额外改动不属于本次门户/PPT 镜像切换。
后续原生资格验收、分发和备份工具也没有自动重建已冻结的六个候选镜像。

## 正确使用构建入口

Host 的仓库根上下文原先通过 `.dockerignore` 排除了 Dockerfile 明确要复制的运行
helpers。最小 BuildKit `FROM scratch` / `COPY` 检查复现了失败；新增专用
`docker/sandbox-ppt-master/Dockerfile.dockerignore` 只放行实际构建源，修后检查通过。
该检查没有安装依赖或完成整个 Office/Chromium/Agent 镜像构建。

当前 Runner 源码构建请使用
[CURRENT-BUILD.md](../docker/ppt-master-runner/CURRENT-BUILD.md)。
`Dockerfile.production-r3-overlay` 和 `RUNNER-LOCK.json` 是历史 R3 记录；
它们的旧 agent 摘要和单文件覆盖不能构建当前确认协议。不能仅更新旧摘要后继续用。
`production-source` 是固定基础镜像上的覆盖源码，不是独立完整的 backend checkout。

普通 Gateway 与独立 Runner 保持不同进程/Deployment/端口和失败域。
新 Runner 入口须明确 `DEER_FLOW_RUNTIME_PROFILE=ppt-master-runner` 与端口 8003，
不能因为基础镜像默认命令是普通 Gateway 就直接沿用其端口 8001。

## 确认功能的实际接通

本轮复现并修复了两处不同的调用链缺口：

- 企业 Runner 原冻结覆盖层没有注册确认接收路由。Gateway 的打开确认页请求
  会转发到 Runner 的相同路径，原接收树返回 404。当前覆盖层加入接收路由，
  保留两跳的用户身份、线程归属、CSRF 和 Host 鉴权。
- 普通 `backend` 的 Runner 入口没有调用已有的持久化 admission 和 monitor，
  仅企业覆盖层有这段集成。当前普通入口也读取 Gateway 核定的任务能力与确认
  协议，把进度写入原有事件流，并在原执行 lease 内观察官方确认与原任务续接。
  功能关闭且输入没有确认标记时保留原路径。

配置就绪与用户同意是两个边界。`/api/features` 仅在开关为 `true`、Gateway
签名密钥有效、服务密钥与配置一致且持久化已初始化时显示确认功能；这些条件
不会替用户开启自动模式。真正的选择仍从同一已认证运行的初始 HumanMessage
与持久化记录核定，不能由模型文本、后续消息或环境变量授予。

当前控制拓扑要求 Runner 和 Host 能访问 `http://gateway:8001`。本地宿主直接
运行而没有该服务 DNS 的环境不属于这条链路的已验证部署。Gateway 保存
`PPT_MASTER_RESPONSES_RELAY_KEY` 用于作用域能力签名，并通过自己的
`SANDBOX_API_KEY` 核验内部服务；不要把签名密钥或上游模型密钥发给普通 AIO
Sandbox。该签名操作本身不请求 DeepSeek 或 Qwen 模型。

普通源码 Runner 的确认路径最多保留八个活动/待核实执行槽。停止或异常后只有
读到同任务、同线程的终态和清理回执才释放执行 lease；不确定时保留 lease 与槽，
容量耗尽则在新任务 admission 前明确拒绝。此时需要运维核实原 Host 进程状态，
不能通过超时计数或盲目重试把忙碌 Sandbox 当作空闲。

以上改动需要新的镜像身份。原六个候选的原生验收和已分发缓存记录继续保留，
但不能用旧 Runner 摘要代表新增接收路由，也不能把本地源码用例或最小 COPY
检查当作新镜像的 ARM64 运行证明。单独构建、记录和验证所变更的组件后，
再更新对应节点的覆盖记录。

Frontend 原有 Dockerfile、依赖锁和默认启动入口没有在原 PR 中改变；GitHub 上原
提交的 ESLint、类型检查和 Next 构建实际通过。`Dockerfile.slim` 是另一种 opt-in
standalone 入口，使用 `/app/frontend` 下的 `node server.js`，不能配上旧的
`cd frontend && pnpm start` 覆盖命令。Gateway rewrite 地址是构建输入，须与目标
环境对应。启用新的员工目录 UI 时，Frontend/Gateway 的接口版本也要配套。

## 检查失败与运行失败分别记录

原 PR 三项检查失败的证据分别为：

- 后端 Ruff：6 个文件的 42 项问题；其中也有 0716 基线继承问题。
  另有 37 个文件的格式差异。风格修正单独保留语义/AST 核对，不删除测试或关闭检查。
- 前端：4 个文件未通过 Prettier；后续 ESLint、TypeScript 和 Next 构建已通过。
- Helm：`config.example.yaml` 已是 32，chart 配置和 README 仍是 31；现同步为 32。
  这修复配置版本检查，不把版本标记说成运行开关。
- 新确认测试会加载真实 vendored Flask 页面 owner，而原后端 dev 依赖未声明
  Flask，能够复现 `ModuleNotFoundError: flask`。现补开发依赖并更新锁文件；
  原有 244 个包的版本和来源不变，没有给 Gateway 增加 Flask 运行依赖。

源码回退不撤销已经执行的数据库迁移。本轮不访问生产数据库，也不据源码回退
推断数据库仍在 0019。生产发布继续使用独立的真实备份/恢复、当前数据库修订、
镜像覆盖与启动门；普通代码回退使用兼容镜像、保留 0020 与确认数据。

## 干净检出与完整单元测试

两组 PPT 来源检查原先依赖本机的历史提交对象；其中 `0044d221` 不在 PR
祖先链中，GitHub 也无法读取该对象。现在随测试保存三份只解析、不执行的原始
源码夹具，核对原提交、Git blob、字节数和 SHA256，再用当前 Python 比较相同的
函数 AST 和允许修改集合。

vendored Skill 从当前 Git index 重建完整 tree，仅把三个授权路径还原为锁定的
基线条目后比较原 tree；公开 Skills 比较完整锁定 tree。工作区差异和未跟踪文件
仍单独检查，文件模式也参与树校验。浅克隆中的检查证明内容边界；实际 PR 的
祖先关系另行审计，不依赖浅克隆缺失的对象。

完整后端检查还暴露了过时测试夹具：权限失败应在发布前拒绝；频道重启失败返回
503 并保留已提交配置；摘要模型必须支持流式接口；模拟抓取 SDK 时还需固定 DNS。
相应测试保留真实权限、SSRF 和摘要断言，补齐浏览器 WebSocket guard 的替身，
并对齐已批准的正式员工域名、Nginx Host 探针和四条 SkillHub 鉴权路由。

前后端单元测试工作流现在也在 Draft PR 上执行，使修补提交获得实际 GitHub
测试结果。发布状态仍由独立的备份、恢复和现场验收决定。

完整 GitHub 后端执行还发现历史迁移测试用当前 ORM 创建所有表、再标成旧版本的
夹具失真：这会提前创建 0020 的表，随后重放迁移时发生重复建表。旧库夹具改为
通过真实 Alembic 历史修订生成，再保留原有去重、字段、漂移和并发断言，明确
要求升级到 `0020_ppt_confirmation`。产品迁移和 bootstrap 不因这些夹具调整而改变。

默认后端测试包含真实 Flask 确认页面的浏览器用例。Playwright 已在原锁文件的
可选依赖中，现也声明为 dev 依赖；未更新其版本或其他已锁定包。开发机首次运行
这些测试时，在 `backend` 下执行：

```bash
uv sync --group dev
uv run --no-sync python -m playwright install chromium
make test
```

Linux CI 使用 `playwright install --with-deps chromium` 准备浏览器及系统库，
保留这五项浏览器测试实际执行。上述命令用于开发机或 CI，不是生产热更步骤。

## 本轮验证及范围

以下定向和本机记录不替代完整单元测试；完整结果以 PR 对应提交的 GitHub
前后端单元测试检查为准。

- Linux Python 3.12 隔离环境的 46 个关联测试文件：702 passed、112 subtests
  passed、23 skipped。其中两项真实浏览器 opt-in 随后已在现有 Chromium 中实际
  执行通过（0 skipped）；其余 21 项需要一次性 PostgreSQL 环境，未在此组合运行。
- 上述 Linux 组合之后补入的停止/取消与八槽容量修复，已用最终源码重跑三个
  关联文件：38 passed、11 subtests passed；实际加载仓库 Runner 模块。
- 完整前端单元测试：163 个文件、1120 passed；使用现有 Windows Node 24、
  pnpm 10.32 和已安装依赖。全量 Prettier、ESLint、TypeScript 检查通过；
  CI 的 Linux Node 22、pnpm 10.26 与 frozen install 是单独的验证环境。
- 后端 Ruff 与格式检查通过，配置版本检查为 32/32。
- 来源检查原四项及十四个篡改负例在完整工作树通过。移除远端、确认两个历史
  提交不可读取的真实 depth-1 克隆中，原四项也通过。来源检查只读 index；
  篡改负例仅操作独立的临时仓库。
- 真实浏览器覆盖官方确认页资源、确认提交，以及关闭浏览器后的 60 秒服务端
  倒计时：只产生一次原生确认。使用临时 SQLite/Flask 和已安装 Chromium；没有
  请求模型或生产服务。
- 针对新接收路由/功能就绪修复，Gateway 与 Runner 的后继镜像在本机 ARM64
  断网容器各通过 8 项实际模块/ASGI 检查；只把 Host socket 换成测试 fixture。
  没有启动应用 lifespan、数据库或模型，不能据此声称生产整链路已重新验收。

这些检查复用已有依赖运行环境及锁定的 Flask 纯 Python 包，没有完成一次新的
全锁文件环境同步，也没有运行整个 monorepo 测试集。未取得维护者当时失败日志，
故不把当前通过结果当作已完整复现并排除该次生产事故的证据。

生产切换仍需现有归档的全文件校验和真实隔离恢复、新镜像在目标节点的缓存覆盖、
以及当前配置、数据库修订、工作负载身份与发布/兼容回退前置检查。PR 保持 Draft；
不自动合并，不因源码检查通过而直接操作生产。
