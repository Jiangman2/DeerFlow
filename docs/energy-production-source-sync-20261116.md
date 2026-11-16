# 油气日报与定时任务生产源码同步

本批基于已合并 PR #51 的企业提交 `5c39443075c007d6bf82148759f0b3b797826eee`，
归档随后至 11 月 16 日已发布的日报和任务运行改动。提交过程不连接生产、不重新发布、
不重放任务、不补发消息。其他会话尚未发布的 MCP、解析器或界面候选不属于本次生产基线。

当前发现目录采用已安装的 `2.1.8-portable` 公开代码包，共 108 文件。部署器另写入
代码清单和企业浏览器绑定，生产为 110 文件，树哈希为
`9ddcf11a813158d00aa119de28955aeeb71c7eca60926b58be5ab67d5579e864`。
浏览器二进制、真实凭据和运行时私有配置不进入 Git。完整父版本仍留存在发现目录外，
原工程、ZIP、历史清单和现场回执不改写。

本次纳入的日报变化包括：目标日无运费时使用此前最近可用报价并明确标注日期；程序集中
处理 prepare、新闻补采校验和 build；取消运行时不必要的逐图 OCR 和重复确认；HTML 与
PNG 使用同一渲染内容并去掉原大幅“数据不完整”横幅。实际缺值、参考日期和来源继续保留
于数据、相应栏目和文字中。新闻仍要求原页与日期证据，不保证供应商或外部搜索永远可用。

网页和 Work 使用当前任务身份生成的真实会话/HTML 阅读链接。阅读入口仍要求登录海能并
沿用对话文件权限；不是匿名分享或任意群成员授权。Nginx 的发布证据绑定实际被两个入口
Service 选中的 `nginx-security-heai-20261012-r1`，而非另一份名为 `nginx` 的 Deployment。
实际入口修复记录与替换脚本保留于 `release/energy-reader-work-repair-20261115-r2/`。
Nginx 配置是运行时覆盖来源，本批不以通用开发配置冒充完整生产配置。

2.1.7 已增加明确授权的群 Webhook 发送器，校验本任务的四件文件、平台发布副本和 HTML
链接，发送一次并保留发送不确定状态。2.1.8 修复普通用户生成 JSON 为 0600、Gateway
以 root 的 `copy2` 发布后普通用户无法读取的边界：仅四件最终报告文件设为 0644，
私有项目、RESULT、缓存、原文和 Webhook 配置仍保持私有。Gateway 文件鉴权不变。
操作员已执行安装、通过 ARM64/UID1000 的 62 项检查、固定及运费参考 PNG，以及一个普通
沙箱的可见性检查；随后明确确认群 Webhook 已成功送达。该确认没有给出新会话标识，不能
强行绑定到之前失败或个人通知的会话，也不等同于手机登录阅读的独立验收。

Gateway 417 的任务会话上下文与 418 的两份 Work 通知模块采用已发布版本。保留原预算
10000。419 只把调度并发配置从 3 调至 32，并开启已有 lease-aware 多实例模式；不改变
运行源码或重放旧任务。配置已提交、滚动已到 70/70，但全量核验在第 50 个之后遇到
`POD_NOT_READY_OR_RESTARTED`：`gateway-747b86d58b-s6mfw` 在北京时间 14:17:53
被 OOMKilled，退出码 137，已重启并就绪。其申请 3Gi、上限 4Gi，Metrics API 不可用。
因此本批只记录“滚动收敛、全量运行验收未完成”，不声明吞吐验收通过、不直接放宽检查，
也不通过删除 Pod 消除重启记录。历史 86 条排队是先前快照，不能当作当前积压量。

生产记录绑定 16 份 Python 源码；其中两份上传解析文件从留存的 Gateway416 源码抽样中
找回，哈希与后续现场清单一致。不能拿更新中的本地镜像同名文件替代它们。Git 根目录
对五份文件做格式化/导入排序及 Python3.12 的两项明确 lint 修正；封存运行字节另存
`deployed-overlay/`，验证器比较哈希和规范化 AST，不声称格式化文件逐字节等于生产。
这只是已观测文件及本批覆盖的源码锁，不声称重建了整个镜像或核验了所有已加载模块。

个人 Work 通知仍有独立未发布修复：会话 `9a762837-e4c4-4376-a14d-fcefbacd4bbc`
的实际消息是带任务名/完成时间/对话链接的通用完成模板，而非要求的单个 HTML 链接。
现场时间与真实 SQL 仓库复现支持“运行先终止、最终文字稍后落库、恢复通知提前入队”的
竞态解释。候选仅保存于
`portable-skills/deployment/personal-notification-ready-20261116-r1/`，没有写入本批
Gateway 业务源码，也未部署。群 Webhook 成功不能证明个人通知链路已修复。

离线检查使用 Python3.12，核对当前源码后才向历史验证器提供精确父版本：

```bash
python -B deploy/enterprise/verify-post51-energy-source-20261116.py --ref HEAD
python -B deploy/enterprise/verify-post44-source-20261113.py --ref HEAD
python -B deploy/enterprise/verify-released-sync-20261110.py --ref HEAD
python -B deploy/enterprise/verify-nov02-released-source-20261103.py --ref HEAD
python -B deploy/enterprise/verify-production-baseline-20261029.py --ref HEAD
```

所有历史激活命令仅用于追溯，不是本次提交需要执行的命令。新检查也不发送模型、行情或
Work 请求；本地的模拟发送、PNG 渲染和现场人工确认分别记录。
