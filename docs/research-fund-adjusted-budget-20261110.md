# 财务报告调整后预算取数修正

本候选修正集团公司报告“科技项目（课题）经费支出情况”首段：原生成器显示 `funding.approved_total`（附录2批准概算，和附录3 B列直接经费预算合计勾稽），而执行率已经使用调整后预算。现在显示同一执行口径的 `totals.target_budget`；标准表导入时该值来自附录3 C列直接经费调整后预算合计。

首段改为“本项目任务合同书批复概预算总额XXX万元（调整后）”，八类支出用途明细改为“批复概预算（调整后）XXX万元”。各明细此前已经优先使用调整后金额，本次补齐其标注。零预算省略以调整后金额为准；显式零和标准表空白金额的零口径不会回退至原预算。

原批复情况和附录2原始金额、附录3 B/C两列、年度预算、用途说明、结余原因和间接经费口径保持。旧版缺失调整后字段的兼容规则未改。国家科技和工信部生成器不变。原 Word/XLSX 模板资产保持原字节，修改在集团公司确定性填充函数中完成。

回归使用合成标准表，经导入、正式检查、Word生成和结构保真检查，覆盖调整后预算增加、减少、为零、空白按零、公式小计、外协父子科目不重复计入、间接经费独立保留。历史源码清单和已上线回执不改写，新候选以独立清单绑定当前文件和旧版本投影。

状态：2026-11-10 操作员回执确认已上线，五个节点读取一致；325 个生产合成回归实例及9项报告命令检查通过，维护 Pod 已清理。Gateway、PPT Runner、Provisioner 部署未修改，业务报告效果仍待用户验收。发布前 SOURCE.json 保持不可变；本次上线另见 release/research-fund-adjusted-budget-20261110-r1/runtime-acceptance-20261110.json，服务器回执已按原序列化重建并匹配 7337 字节及 SHA256。历史报告不会自动改写，GitHub 同步尚未完成。

本地验证：财务行为与版本链共418项测试、128项子测试通过。合成Word通过结构保真检查；本机打包的LibreOffice渲染入口因缺少 `soffice.exe` 不可用，改用独立隐藏的 Microsoft Word 只读导出PDF，检查16页概览及本次变更所在的第5、6页，新增文字无截断或重叠。这不是生产Linux排版验收。

离线核验入口：

```bash
python -B deploy/enterprise/verify-research-fund-production-source.py --worktree
python -B deploy/enterprise/test_research_fund_adjusted_budget_source_20261110.py
python -B -m pytest tests/skills/test_research_fund_adjusted_budget.py
```
