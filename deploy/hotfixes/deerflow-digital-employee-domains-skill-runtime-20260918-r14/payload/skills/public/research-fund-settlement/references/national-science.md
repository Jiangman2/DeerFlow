# 国家科技项目

类型值：`national_science`。示例中的“国家重点研发计划”是可能的计划名称，不应限制本类型。

## 九个栏目

1. `national.entity_overview` 课题承担单位基本情况
2. `national.project_implementation` 课题立项及实施情况
3. `national.accounting` 课题资金核算情况
4. `national.internal_controls` 单位与课题资金管理相关的内部管理制度建设及执行情况
5. `national.budget_adjustments` 中央财政资金预算安排及调剂情况
6. `national.funds_received` 中央财政资金到位情况
7. `national.funds_transferred` 中央财政资金拨付情况
8. `national.fund_usage` 中央财政资金使用情况
9. `national.payables_forecast` 中央财政资金应付未付及预计后续情况

## 关键口径

- 区分课题所属项目名称、课题名称和课题编号。
- 区分中央财政资金与其他来源资金，使用 `funding.sources[].kind` 标记。
- 若费用表只覆盖中央财政资金，设置 `funding.expense_scope` 为“中央财政资金”。
- 预算调剂必须记录申请、批准主体、批准日期和制度依据；没有调剂时也要有材料支持。
- 账面累计支出与审计认定支出分别保存。
- 应付未付和预计后续支出分别保存，不能并入截止日实际支出。

## 默认费用分类

直接费用：设备费、材料费、测试化验加工费、燃料动力费、差旅/会议/国际合作交流费、出版/文献/信息传播/知识产权事务费、劳务费、专家咨询费；另列间接费用。

## 正式稿至少需要

- 承担单位基本信息、课题名称/编号/周期/负责人和研究内容；
- 中央财政资金预算、到账、拨付和支出；
- 费用明细及审计认定；
- 核算系统、单独核算方式、内部制度执行；
- 应付未付与预计支出数据或经证据支持的零余额结论。
