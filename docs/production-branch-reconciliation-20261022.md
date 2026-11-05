# 2026-10-22 生产分支源码对齐核对

目标：`codex/enterprise-consolidation-20260716` 保存当前实际运行版本的源码与必要部署依据。尚未上线的功能继续保留在待发布分支；本次核对不更新生产服务、不执行数据库迁移。

## 当前线上基线

用户提交的 `结果.txt` 对应北京时间 2026-10-22 19:03:58 的采集。37 次查询、耗时 8.335 秒，采集前后工作负载 spec 与 ConfigMap 内容稳定。13 个已采集工作负载 spec 和全部 14 个 ConfigMap 内容摘要均与 10 月 21 日记录一致。

| 组件 | generation / 就绪 | 实际版本 |
| --- | --- | --- |
| Gateway | 382 / 70 | `eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5` |
| Frontend | 225 / 2 | `ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5` |
| PPT Runner | 54 / 1 | `8a853325c46a215c06d0ab77720aa3a8e48b06c98c60f2837c663867e704b0f0` |
| WeKnora UI | 37 / 1 | `preupload10-f25aea4dfb29-20261020062633`；Pod imageID `sha256:de5074d6b353514dc190063b2cd27fd464b12c160b494d26c57b1bbe7b68860e` |

元数据是当前镜像/配置证据，不是全部运行文件的内容证明。采集范围不含所有 Sandbox、MinerU 等组件，也不读取数据库有效配置。

## 分支历史：存在实际撤回，不是合并时漏文件

核对 PR HEAD `5862bb6936d4d736f2f1c0beb79afd5ed4cf317c` 与目标 HEAD `28a9f821b476cc3c378c3b3c6ff26ccf9619b42a`：

1. `721a9f7c2` 在 10 月 21 日合入了 PR 当时的 `d7d119af6`。两者完整 Git 文件树均为 `103ef4c3fe4742380678be5aeeabf011cb14b6e6`，内容完全一致。
2. `f8b06b6d2` 添加同事的六个 WeKnora UI 文件修改。
3. `28a9f821b` 明确撤回热更合并。撤回后的完整目标树等于合并前 `aca436e49` 加上这六个 WeKnora UI 修改；WeKnora UI 目录外没有保留下来的差异。

因此当前目标分支不能被当作当前生产源码。Git 历史虽保留热更提交为祖先，工作树内容已撤回；只检查 commit 是否存在会产生错误结论。

## 已发布源码锁对照

此前已核实的 17 项发布源码记录，本轮比较 Git blob：PR HEAD 有 14 项与对应发布源码相同；目标分支 17 项均不同于对应发布记录，其中 7 项文件不存在。

PR 的三项不同有明确后续变更：

- `community/appforge/data_qa_stream.py` 和 `tools.py`：`2e24bd7b3` 的问数实时进度候选，尚未上线。
- `routers/employee_directory.py`：`ad40f1f88` 将旧员工名换成智能穿透审计助手。旧 UUID 发布源码锁早于此变更，需以当前运行文件摘要核对，不能仅凭旧锁判断遗漏或已部署。

OA v2、多文件数据库扩展、OA 兼容启动补丁和 Token 等待状态显示也尚未上线。历史候选/预载回执不等于生产切换回执。

## 重新直接合并的风险

`git merge-tree` 仅读取双方提交并生成模拟合并树，没有改变工作树或分支。结果有 11 个冲突；模拟树仍缺少 7 个已发布源码路径，包括 DataLake 来源中间件、GLM 适配器/结束校验和员工目录路由。不能仅解决提示的冲突后就声称已恢复生产代码。

后续对齐应从当前目标分支出发，保留同事 WeKnora 六文件修改，按当前镜像源码逐组件补回。不要整包重合待发布 PR，也不要重置同事分支。

## WeKnora UI 的证据边界

同事提交涉及每批最多 10 文件（含子目录、拖拽及追加选择）、提前显示限制/已选数量提示，以及公开静态文件与 config.js 读取权限。忽略行尾差异后属于六文件的有限改动，不应把显示的数千行 CRLF 差异当作全量重写。

用户确认暂无该生产镜像的构建记录。当前只能确认这六文件已在目标分支，以及服务器仍运行 preupload10；不能证明它们构建后与该镜像静态资源完全相同。先保留源码，采集运行静态文件指纹，后续以构建产物或可信源码归档补齐对应关系。

## 证据与下一步

- `deploy/enterprise/current-production-observation-20261022.json`：本次脱敏现场快照及输入文件 SHA-256。
- `deploy/enterprise/pr13-branch-release-comparison-20261022.json`：固定提交、逐文件 blob 比较与待核实项。
- `deploy/enterprise/pr13-merge-simulation-20261022.json`：模拟树与冲突路径。
- `deploy/enterprise/inspect-production-runtime-sources.py`：绑定本次三个部署基线，读取一个就绪 Pod 的 Python 源码哈希及 WeKnora UI 静态文件哈希；不输出文件正文、环境变量或密钥，不调用业务接口。

在运行源码指纹回执到达前，不将部分发布锁覆盖声明为整个分支已经与生产一致。待发布分支及其已准备镜像保持原状。

## 运行源码回执与恢复结果（2026-10-22）

第二份现场回执 `结果(1).txt` 成功取得 Gateway、PPT Runner、WeKnora UI 各一个就绪 Pod 的指纹。部署、容器身份和采集前后基线均通过采集器核对。它不证明其他副本的运行文件，也不证明依赖、配置和完整镜像可重建。

| 范围 | 目标分支原状态 | 独立对齐分支当前状态 |
| --- | --- | --- |
| Gateway Python | 579 项中 528 相同、26 不同、25 缺失 | 主源码目录 579/579 哈希一致，Python 3.12 语法通过，无额外 Python 文件 |
| PPT Runner Python | 主源码目录对比并非其专用构建源 | 独立生产快照 550/550 哈希一致，Python 3.12 语法通过，无额外 Python 文件 |
| WeKnora UI | 六份同事改动已存在 | 保留六文件；取得 164 个静态文件/入口脚本摘要，尚无构建对应证明 |

Gateway 的 `runtime/interrupted_history.py` 和 `runtime/journal.py` 已从本地保留的 `9b3a6d80…` 历史镜像中取得，并与当前服务器原始字节 SHA-256 完全一致。因此额外两文件导出工具无需执行。发布包来源记录、Git blob 和本地镜像来源记录见 `source-restoration-20261022.json`。

本次恢复在 `codex/production-source-alignment-20261022` 独立工作分支完成；用户随后明确要求推送并合入 `codex/enterprise-consolidation-20260716`。Gateway 主目录恢复 51 项差异；Runner 使用独立源码快照，不能把它覆盖到 Gateway 主目录。原来的 9 月 Runner 构建锁仍是历史记录，不把它伪装为当前镜像构建依据。

验证命令（Python 3.12+）：

```bash
python deploy/enterprise/verify-production-source-snapshot.py
```

未将上述结果扩大为“整个产品与镜像完全一致”：前端完整构建溯源、WeKnora UI 构建对应、非 Python 运行文件、依赖锁和生产配置有效值仍需分别核对。没有部署、数据库操作或模型调用。

校验边界：本次执行逐文件哈希、完整 Python 文件集合及 Python 3.12 语法检查，没有宣称已通过完整应用回归或 CI。当前复用的本地虚拟环境未安装 Ruff。Runner 原始 `lead_agent/prompt.py` 有一行尾部空格，为保持服务器源码哈希保留；归档范围的空白差异不应被误报成检查全部通过。

## 合并时的并发修改核对

推送前重新获取目标分支，发现新提交 `f9ea6eacd`（系统管理员豁免 WeKnora 上传批次数量限制）。已无冲突合入，四个 WeKnora 文件与该远端提交完全相同。本次集成后的 Gateway 579 项、Runner 550 项哈希及 Python 3.12 语法检查再次通过。这不改变前端构建对应关系仍待核实的结论，也不代表执行过生产部署或全量 CI。
