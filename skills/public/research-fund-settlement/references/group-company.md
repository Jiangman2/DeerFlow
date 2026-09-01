# 集团公司（有限公司）项目

类型值：`group_company`。

## 关键口径

- `project.entity_mode = group`：集团公司项目，通常按含税口径，`funding.tax_basis = tax_included`。
- `project.entity_mode = limited_company`：有限公司项目，通常按不含税口径，`funding.tax_basis = tax_excluded`。
- 如任务书或批复明确采用其他口径，以文件为准，并在 `assumptions` 记录用户确认。
- “任务合同书批复概预算”“分年正式下达预算”“调整后预算”“实际支出”必须分开。

## 九个栏目

1. `group.budget_approval` 概预算批准情况
2. `group.budget_changes` 概预算变更情况
3. `group.annual_approvals` 科技项目实际批复情况
4. `group.overall_settlement` 支出（预）决算总体情况
5. `group.expense_breakdown` 支出明细项及结构总体说明
6. `group.balance_reasons` 资金结余及原因
7. `group.management_basis` 经费管理依据的制度情况
8. `group.management_status` 经费管理情况及存在问题
9. `group.finance_opinion` 承担单位财务部门意见

## 费用分类

默认使用八类：设备与软件费、材料/燃料动力费、外协实验/试验费、外委研究费、出版/文献/信息传播/知识产权事务费、会议/差旅/国际合作交流费、专家咨询评审/劳务费、其他费用。

调整后预算与支出差异达到 10% 时必须填写 `variance_reason`。外委研究费还应说明其占直接经费比例、系统外委托比例和受托单位。

## 正式稿至少需要

- 项目名称、任务合同依据、承担单位、截止日；
- 批复总额、调整后总额、资金来源和分年正式下达预算；
- 各费用科目调整后预算、实际/审定支出及证据；
- 预算变更情况或“经核实无变更”的来源；
- 财务管理制度、核算方式和财务部门意见。
