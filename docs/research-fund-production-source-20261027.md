# 财务决算 Skill 生产源码与上线记录（2026-10-27）

本次将 `research-fund-settlement` 已上线的三项修改补回
`codex/enterprise-consolidation-20260716`。范围仅包含此 Skill、相关测试、
CI 检查与发布证据；不包含暂缓的 Work PR #16、Work 候选镜像、迁移或路由更新。
本次 GitHub 归档不再次操作生产服务器。

## 已上线行为

1. 报告全文显式使用黑色，包括封面、标题、正文、表格、目录及页眉页脚。
   清除主题字体色和文本填充色，其他版式仍受模板保真校验约束。
2. 集团公司报告仅在“支出明细项及结构总体说明”和“资金结余及原因”中，
   删除有效批复概预算明确为零的科目及附属说明，并连续编号。
   有调整预算时采用调整预算，否则采用原预算；未知金额不视为零。
   原始记录、表格数据以及非零结余、超支原因校验继续保留。
3. 取消 `ANNUAL_APPROVED_TOTAL_MISMATCH`：批准概算额允许与各年度批准额之和不同，
   两者分别据实填列；年度批复分析使用年度金额的实际合计。其他勾稽继续校验。

## 发布与收尾证据

生产修改是共享 Skills PVC 上的单个 Skill 更新，没有更换服务镜像、滚动服务、
修改数据库或调用模型。发布前建立并核验备份，实际存储不支持交换重命名，
因此采用两次重命名切换，期间存在短暂目录名称空档。

服务器运行了三种报告类型各自的 `check/build/inspect`，共 9 项合成检查，全部通过。
发布后在 `ecs-zdz-pro-0018` 至 `0022` 各抽样一个 Gateway，五个节点均读取到
相同的新 Skill 文件树。该证据不等于对所有历史会话或真实业务报告完成验收。

| 对象 | SHA256 |
| --- | --- |
| 更新前 28 文件树 | `db91b903a0f8250aecb7aaa4bbdf2b5aeaa871686d9f08862e5dcfb21d1a5da0` |
| 已上线 29 文件树 | `33ca005e82b4c9cae70fe6745b186447c54c323925d9e193605406653b6905be` |
| 发布压缩包 | `7cfdae4c41032f2a3a3da23786a28c561a8e481a2822bb92d45ad80256e81946` |
| 原始更新回执 | `1fbbdcb5a75baa8253729274abb08345c34991542df4b58d23e3ea805540bf55` |

原始回执保留 `status=INCOMPLETE` 和 `KUBERNETES_COMMAND_NOT_ACCEPTED`：
Skill 发布、备份核验及五节点核验均已完成，失败位于最后的临时维护 Pod 删除命令。
操作员随后单独删除 `fund-skill-maint-a991a551af52`，终端确认请求成功。
`--wait=false` 不证明 Pod 已完全消失，因此收尾记录仅声明删除命令已受理。
不要重新执行已用过的一次性发布脚本。

原始服务器报告保存在
`/var/lib/portal-research-fund-skill-update-20261027-r1/REPORT.json`，
备份位于
`/mnt/skills/.research-fund-release-20261027-r1-a991a551af524e22a35329aee096d2a4/backup`。
归档回执只还原终端粘贴中的一个 Markdown 镜像链接，并去掉持久化之后追加的
三个报告元数据字段；还原后的 6,486 字节与服务器给出的 SHA256 完全相同。

## 可重复核对

生产锁为
[`research-fund-production-source-lock-20261027.json`](../deploy/enterprise/research-fund-production-source-lock-20261027.json)，
历史工具及证据在
[`release/production-source-20261027/research-fund-settlement/`](../release/production-source-20261027/research-fund-settlement/)。
准备阶段的 `NOT_DEPLOYED`/`PENDING` 记录保持原样，最终状态应结合更新及人工清理回执判断。
发布脚本内含已核验的压缩包；不重复归档临时构建树、缓存或生成的测试报告。

```bash
python deploy/enterprise/verify-research-fund-production-source.py --ref HEAD
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python -B -m pytest \
  tests/skills/test_research_fund_settlement.py \
  tests/skills/test_research_fund_black_text.py \
  tests/skills/test_research_fund_zero_budget.py \
  tests/skills/test_research_fund_annual_budget.py \
  backend/tests/test_research_fund_settlement_skill.py -q --confcutdir=tests/skills
```

源码核验直接读取指定 Git 提交，比较完整文件集合、逐文件字节摘要、原始上线回执和
历史工具摘要，不依赖工作区换行设置。功能测试覆盖三类模板、黑色字体、非法格式修改、
零预算/未知预算/调整预算以及允许年度批复总额不同但继续拒绝其他勾稽错误。

真实业务验收仍待新建任务生成报告后确认；已经生成的旧报告不会自动变化。

## 原有模板的 CI 审查

原静态扫描将五份未改动的 DOCX/XLSX 参考资产统一报为嵌套压缩包。
本次保留原始扫描输出，增加仅用于 CI 的资产复核：固定这五个路径及已审查的
SHA256，逐个有界展开，拒绝宏、嵌入对象、外部关系、执行文件、路径越界、
XML 实体和未支持的二进制成员。全部核验通过才将对应的五条归为已审查资产。
新增/变化的模板、其他错误和不完整扫描继续失败；三个原有提示仍保留。
运行中的 Skill 审查器、Gateway 业务代码及已上线 Skill 字节均不因此改变。
