# 财务与油气日报后续源码同步

本批基于企业分支 `f882aa44b96a8aa7e35008a683677c7e55d2ddf8`，补齐 PR #44 后本会话完成的工作，保留 #45、#46、#49、#50 的全部代码。提交和 CI 均不执行生产发布、不访问生产数据库、不调用行情或模型、不发送 Work 消息。

财务报告已于 11 月 10 日更新：集团公司经费支出首段和用途明细标明“调整后”，总额使用附录 3 C 列的调整后预算，与预算执行率保持同一口径。显式零或标准表空白的零值不回退 B 列；继续省略零预算科目。原批复、用途和结余原因等其他规则保留。五节点与合成报告检查通过的操作员回执单独归档，实际用户报告效果仍待验收。

油气日报当前源码为已安装的 `2.1.0-portable`，默认北京时间期次、前一日行情。并行批量采集、一次集中整理新闻、确定性出图和四文件交付，减少模型反复探测和重复选新闻。供应商明确休市才标休市，技术缺失生成有醒目标识的不完整报告，不以旧价补当期。程序生成与平台交付分别判断；四件交付后组成唯一完整文字 `delivery/final.md`，保留新闻原页超链接、缺数说明和宿主真实对话链接。

公开源码为 107 文件；生产安装器另外写入一份企业浏览器绑定，共 108 文件。ARM64 的普通用户浏览器依赖位于真实 `public` 挂载范围，经 UID 1000、相同挂载方式和固定样例 PNG 验证；二进制不提交 Git。密钥仅由运行环境／私有配置提供，公开包、Git、回执和最终文字均不包含实际凭据。安装工具与之前失败回执保留，用于追溯，不应重新执行。

Gateway 六文件改动使用当前任务身份生成真实 URL，文件交付回执、Work 普通回复和定时通知使用“点击查看完整日报”显示文本。它保留已有本人私聊绑定、发送回执、长度限制和不确定发送不盲目重试规则。源码与 Gateway 415 只读采样中的六份挂载逐一匹配，后续预算切换保留这些来源；其中两份仅作 Ruff 格式化，原字节和 AST 相等证明分开保存。本次不修改同事新镜像、数据湖、上传或监控实现。

旧版日报从发现目录中移除，完整 158 文件原工程、原 ZIP、旧薄入口和历史发布清单转存 `release/post44-source-sync-20261113/parent/`，逐字节保留。当前源码先通过新清单，再向旧验证器投影准确的父版本；不改写原清单或让新版本冒充历史验收。CI 对当前公开 Skill 的五处固定静态扫描发现进行精确审查：三处为环境／私有文件凭据读取，两处为 argv 引号处理和固定同文件子进程；仅在完整源码哈希及发现集合匹配时接受，平台运行时扫描规则不变。

定时任务预算 10000 **已由操作员只读补验确认部署**。2026-11-13 16:57:38（北京时间），r5 返回 `SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED`：Gateway 416 已收敛，70 个更新副本全部就绪、可用，无终止副本，70×13 份固定源码和服务端点均通过。镜像、CA 引用及除预算 ConfigMap 引用之外的 spec 保留，原 ConfigMap 未修改。后续新定时运行的预算配置为 10000，全局上限仍为 10000；已有运行配置未改变，也未重放任务。

原激活终端已关闭，本地没有原激活末尾 stdout 或下载的完整服务器控制目录。新增 `release/scheduler-budget-20261113-r5/OPERATOR-READONLY-ACCEPTANCE-20261113.json` 如实归档操作员粘贴的补验 JSON，以原 operation、plan SHA 和第六份 journal SHA 绑定这次切换；不伪造服务器完整报告。该次补验本身的 patch/create/write/provider/Work 计数均为 0。它核验磁盘源码、配置、Pod 和端点，不证明已加载 Python 模块或新定时任务的完整业务成功；这两项仍按回执保留边界。

此前工具失败回执、发布前 SOURCE 和“待部署”快照保持不可变。历史验证器的 `scheduler_budget_deployed=false` 表示封存时状态，不覆盖后续现场事实；当前 `verify-post44-source-20261113.py` 另校验精确补验回执及工具哈希并输出已部署状态。r5 仅兼容标准只读 ServiceAccount 挂载和自身 hash 调度条件，其他变化仍拒绝。无需再执行激活命令。

离线核验：

```bash
python -B deploy/enterprise/verify-post44-source-20261113.py --ref HEAD
python -B deploy/enterprise/verify-released-sync-20261110.py --ref HEAD
python -B deploy/enterprise/verify-research-fund-production-source.py --ref HEAD
python -B deploy/enterprise/verify-nov02-released-source-20261103.py --ref HEAD
python -B deploy/enterprise/verify-production-baseline-20261029.py --ref HEAD
python -B portable-skills/diagnostics/energy-market-daily-2.1.0-final-text-r1/run_candidate_regressions.py
python -B portable-skills/diagnostics/energy-market-daily-2.1.0-final-text-r1/test_final_text.py
python -B portable-skills/deployment/scheduler-budget-20261113-r5/test_scheduler_budget.py
```

ARM64 新鲜取数及 PNG 资格、普通沙箱可见性和 Skill 2.1.0 安装回执均为历史现场证据；不等于已验收每天 7 点的全链路推送，也不证明自定义群 Webhook 已收到整段最终正文。未创建新定时任务或补发历史消息。
