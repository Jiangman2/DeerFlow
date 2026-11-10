# 财务报告调整后预算取数修正

本候选修正集团公司报告“科技项目（课题）经费支出情况”首段：原生成器显示 `funding.approved_total`（附录2批准概算，和附录3 B列直接经费预算合计勾稽），而执行率已经使用调整后预算。现在显示同一执行口径的 `totals.target_budget`；标准表导入时该值来自附录3 C列直接经费调整后预算合计。

首段改为“本项目任务合同书批复概预算总额XXX万元（调整后）”，八类支出用途明细改为“批复概预算（调整后）XXX万元”。各明细此前已经优先使用调整后金额，本次补齐其标注。零预算省略以调整后金额为准；显式零和标准表空白金额的零口径不会回退至原预算。

原批复情况和附录2原始金额、附录3 B/C两列、年度预算、用途说明、结余原因和间接经费口径保持。旧版缺失调整后字段的兼容规则未改。国家科技和工信部生成器不变。原 Word/XLSX 模板资产保持原字节，修改在集团公司确定性填充函数中完成。

回归使用合成标准表，经导入、正式检查、Word生成和结构保真检查，覆盖调整后预算增加、减少、为零、空白按零、公式小计、外协父子科目不重复计入、间接经费独立保留。历史源码清单和已上线回执不改写，新候选以独立清单绑定当前文件和旧版本投影。

状态：候选，尚未发布到生产。没有执行安装、重启、数据库修改或改写已有报告。

本地验证：财务行为与版本链共418项测试、128项子测试通过。合成Word通过结构保真检查；本机打包的LibreOffice渲染入口因缺少 `soffice.exe` 不可用，改用独立隐藏的 Microsoft Word 只读导出PDF，检查16页概览及本次变更所在的第5、6页，新增文字无截断或重叠。这不是生产Linux排版验收。

离线核验入口：

```bash
python -B deploy/enterprise/verify-research-fund-production-source.py --worktree
python -B deploy/enterprise/test_research_fund_adjusted_budget_source_20261110.py
python -B -m pytest tests/skills/test_research_fund_adjusted_budget.py
```
