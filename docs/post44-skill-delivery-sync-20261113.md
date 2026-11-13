# 财务与油气日报后续源码同步

本批基于企业分支 `f882aa44b96a8aa7e35008a683677c7e55d2ddf8`，补齐 PR #44 后本会话完成的工作，保留 #45、#46、#49、#50 的全部代码。提交和 CI 均不执行生产发布、不访问生产数据库、不调用行情或模型、不发送 Work 消息。

财务报告已于 11 月 10 日更新：集团公司经费支出首段和用途明细标明“调整后”，总额使用附录 3 C 列的调整后预算，与预算执行率保持同一口径。显式零或标准表空白的零值不回退 B 列；继续省略零预算科目。原批复、用途和结余原因等其他规则保留。五节点与合成报告检查通过的操作员回执单独归档，实际用户报告效果仍待验收。

油气日报当前源码为已安装的 `2.1.0-portable`，默认北京时间期次、前一日行情。并行批量采集、一次集中整理新闻、确定性出图和四文件交付，减少模型反复探测和重复选新闻。供应商明确休市才标休市，技术缺失生成有醒目标识的不完整报告，不以旧价补当期。程序生成与平台交付分别判断；四件交付后组成唯一完整文字 `delivery/final.md`，保留新闻原页超链接、缺数说明和宿主真实对话链接。

公开源码为 107 文件；生产安装器另外写入一份企业浏览器绑定，共 108 文件。ARM64 的普通用户浏览器依赖位于真实 `public` 挂载范围，经 UID 1000、相同挂载方式和固定样例 PNG 验证；二进制不提交 Git。密钥仅由运行环境／私有配置提供，公开包、Git、回执和最终文字均不包含实际凭据。安装工具与之前失败回执保留，用于追溯，不应重新执行。

Gateway 六文件改动使用当前任务身份生成真实 URL，文件交付回执、Work 普通回复和定时通知使用“点击查看完整日报”显示文本。它保留已有本人私聊绑定、发送回执、长度限制和不确定发送不盲目重试规则。源码与当前 Gateway 415 只读采样中的六份挂载逐一匹配；其中两份仅作 Ruff 格式化，原字节和 AST 相等证明分开保存。本次不修改同事新镜像、数据湖、上传或监控实现。

旧版日报从发现目录中移除，完整 158 文件原工程、原 ZIP、旧薄入口和历史发布清单转存 `release/post44-source-sync-20261113/parent/`，逐字节保留。当前源码先通过新清单，再向旧验证器投影准确的父版本；不改写原清单或让新版本冒充历史验收。CI 对当前公开 Skill 的五处固定静态扫描发现进行精确审查：三处为环境／私有文件凭据读取，两处为 argv 引号处理和固定同文件子进程；仅在完整源码哈希及发现集合匹配时接受，平台运行时扫描规则不变。

定时任务预算 10000 **仍是待部署候选**。之前工具在修改前停止；r5 适配 Kubernetes 补入的标准只读 ServiceAccount 挂载和自身 `pod-template-hash` 调度条件，其他变更继续拒绝。工具保留 Gateway 415 的镜像、13 份核心／覆盖源码、CA 引用、完整 spec CAS 和 70 Pod／服务端点补验，计划更新至 416。没有成功激活回执，不能宣称生产已改为 10000；也不重放已有任务或改变已捕获运行配置。

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
