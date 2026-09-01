# 数据合同与证据要求

`case.json` 是报告唯一事实源。模型负责从材料提取事实和撰写受证据约束的说明，脚本负责金额归一化、计算、校验和 Word 生成。

## 顶层结构

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `1.0` |
| `profile` | `group_company`、`national_science` 或 `miit` |
| `report` | 报告日期、截止日、编制/复核信息和状态 |
| `project` | 项目名称、编号、批复、周期、研究内容与完成情况 |
| `organizations` | 牵头/承担单位和参研单位 |
| `funding` | 批复、调整、资金来源、到位、分年下达、调剂和转拨 |
| `expenses` | 费用科目预算、报审/实际、审定金额与差异原因 |
| `management` | 制度、核算、内控和财务管理事实 |
| `procurement` | 采购、合同、招投标事实 |
| `audit` | 报审、审减、审定、应付未付和预计支出 |
| `assets` / `outcomes` | 形成资产和科研成果 |
| `narratives` | 按栏目 ID 保存的证据约束说明 |
| `selected_sections` | 需要输出的栏目 ID；空数组表示本类型全部栏目 |
| `evidence` | 来源文件和定位清单 |
| `conflicts` | 尚未解决的来源冲突 |
| `assumptions` | 用户明确接受的口径假设，不得混入事实 |

完整机器结构见 [../schemas/case.schema.json](../schemas/case.schema.json)，栏目映射见 [profile-rules.json](profile-rules.json)。使用 `report_cli.py init` 生成可填写骨架，避免手写结构漂移。

## 金额对象

所有金额写为 JSON 数字，统一单位为万元。不要写入“100万元”这样的字符串。

资金来源示例：

```json
{
  "name": "中央财政资金",
  "kind": "central_finance",
  "approved": 500,
  "received": 500,
  "spent": 470,
  "audited": 468,
  "evidence_refs": ["E1", "E2"]
}
```

费用明细示例：

```json
{
  "code": "equipment",
  "name": "设备费",
  "budget": 100,
  "adjusted_budget": 95,
  "reported": 92,
  "actual": 92,
  "audited": 90,
  "description": "用于……",
  "variance_reason": "通过集中采购节约5万元",
  "evidence_refs": ["E2", "E3"]
}
```

脚本统一计算：

- `approved_total = Σ funding.sources[].approved`；
- `received_total = Σ funding.sources[].received`；
- `reported_total = Σ expenses[].reported`，缺失时回退到 `actual`；
- `audited_total = Σ expenses[].audited`；
- `audit_reduction = reported_total - audited_total`；
- `balance = adjusted_budget - audited/actual`；
- `execution_rate = audited/actual ÷ adjusted_budget × 100%`。

报告范围只覆盖某一资金来源时，设置 `funding.expense_scope` 为该来源名称；否则使用 `all_funds`。

## 证据

证据项格式：

```json
{
  "id": "E1",
  "source_file": "项目任务书.pdf",
  "locator": "第12页，预算表",
  "fact": "项目批复总经费500万元",
  "as_of": "2026-08-31"
}
```

正式稿中，资金来源和每个费用明细至少各有一个有效 `evidence_refs`。引用 ID 必须存在于 `evidence`。

## 冲突与缺失

- `null` 表示未知；`0` 只表示材料明确为零。
- 任务书预算、正式下达预算、财务账实际支出、审计认定支出是不同概念，分别保存。
- 冲突对象至少包含 `field`、`values`、`sources` 和 `resolution`。`resolution` 为空时正式稿失败。
- 初稿允许 `[待补充：字段名]`；正式稿禁止任何占位符。

## 受控说明文字

`narratives` 的键必须是栏目 ID，例如 `miit.governance`。文字可以解释事实、差异原因和制度执行，不得：

- 将示例里的结论当作本项目事实；
- 把“未提供问题”写成“未发现问题”；
- 把预算下达等同于资金到账；
- 把账面支出等同于审计认定支出；
- 省略超支/结余达到阈值的具体原因。
