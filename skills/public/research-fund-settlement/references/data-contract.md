# 数据合同与证据要求

`case.json` 是报告唯一事实源。模型负责从材料提取事实和撰写受证据约束的说明，脚本负责金额归一化、计算、校验和 Word 生成。

## 顶层结构

| 字段 | 含义 |
|---|---|
| `schema_version` | 固定为 `1.0` |
| `profile` | `group_company`、`national_science` 或 `miit` |
| `input_contract` | 集团公司标准输入表的版本、文件哈希、工作表和校验状态；其他类型可省略 |
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
  "scope": "direct",
  "parent_code": null,
  "include_in_total": true,
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

`scope` 为 `direct` 或 `indirect`。`include_in_total = false` 只用于父科目的拆分明细，避免父子项目重复汇总；`parent_code` 指向父科目。`input_row` 和 `input_balance` 可保存标准表行号及原表结余，用于审计追踪。

集团公司正文中的 `description` 是费用用途的项目补充说明，例如有证据的外协合同数量、名称和支撑研究内容，接在原模板通用用途后；`variance_reason` 单独供结余原因章节使用（设备费的有结余说明除外）。两者不能互相代填。没有项目明细时不推断合同数量或单位名称，也不输出 `XXX`。

脚本统一计算：

- `approved_total = Σ funding.sources[].approved`；
- `received_total = Σ funding.sources[].received`；
- `reported_total = Σ expenses[].reported`，缺失时回退到 `actual`，且只累计 `include_in_total != false` 的项目；
- `audited_total = Σ expenses[].audited`；
- `audit_reduction = reported_total - audited_total`；
- `balance = adjusted_budget - audited/actual`；
- `execution_rate = audited/actual ÷ adjusted_budget × 100%`。

集团公司新版标准表的 `input_contract.budget_execution_scope` 固定为 `direct_only`：以上预算执行总计只累计直接经费，`funding.adjusted_total` 与 `audit.reported_expenditure` 也采用相同口径。间接经费的原始费用行和各自小计完整保留，另列展示；不得通过删除间接费用或把其 `include_in_total` 改成 false 来调平。新合同缺少该口径时须重新导入。历史 `2026-09`／`2026-11` 案例无此标志时保留原计算口径，不静默改写历史报告。其他两类项目不应用这一集团公司规则。

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

- `null` 表示未知；`0` 表示材料明确为零，或集团公司标准表依据用户已确认口径将空白金额归零。不得将“未知”等文本、未提供的审计结果或其他类型材料中的缺失事实归零。
- 集团公司导入合同 `2026-11-05` 增加编制日期和附录4，仍兼容已通过校验的 `2026-09`、`2026-11` 合同。合计、结余空白由脚本推导；原表有数值或公式时继续核对，不覆盖差异。
- `project.period_text` 保存集团公司表内项目周期原文并用于封面；可识别的完整日期另存 `start_date`／`end_date`，仅有年份、月份或其他说明时不补造具体日期。
- `project.type_text` 保存附录1“项目类型”的非空原文并直接用于封面，不限定名称、不添加后缀。它与内部 `entity_mode` 模板选择分开；现有分类和金额处理保留，自定义名称沿用标准表的有限公司模板。历史 case 缺少原文字段时保留旧封面行为，重新导入原表后才采用原文，不改写历史报告。
- 任务书预算、正式下达预算、财务账实际支出、审计认定支出是不同概念，分别保存。
- 集团公司附录2的批准概算额与年度批准额之和可以不同，不作为金额冲突或勾稽错误；批准概算、各年度金额和年度实际下达汇总分别显示，其他汇总等式仍须成立。
- 冲突对象至少包含 `field`、`values`、`sources` 和 `resolution`。`resolution` 为空时正式稿失败。
- 初稿允许 `[待补充：字段名]`；正式稿禁止任何占位符。
- 集团公司项目缺少通过校验的 `input_contract` 时，初稿给出警告、正式稿直接失败。
- 集团公司项目的 `project.acceptance_date` 是兼容历史记录的可选原始字段，不参与必填、日期格式或与编制日期先后关系校验。`report.prepared_date` 仍须有效；不要为生成报告补造验收日期。

集团公司外协实验／试验费父级的 `variance_reason` 由两个子项已填原因按顺序汇总。`input_variance_reason` 保留原父级文字，`variance_reason_source_rows` 记录实际子项行号；不覆盖上传原件。非零结余子项缺原因仍报错，不能用父级概括代替。

## 集团公司附录4与预算变更

`funding.outsourcing` 由附录4导入，包含 `total`（外委支出）、`external_amount`（系统外金额）、`external_share`（系统外占外委支出的0至1比例）、`external_organizations`（真实单位名称数组）及 `evidence_refs`。未提供附录4时不添加该对象，不按0补齐。正系统外金额必须有单位与来源；金额、比例及与附录3的口径矛盾仍阻断。

脚本在 `_derived.outsourcing` 计算三项百分数：`commissioned_to_direct_percent`、`external_to_direct_percent`、`external_to_commissioned_percent`。前两项用于正文，后一项对应输入表；使用报告的认定支出口径。不要人工计算或写入派生字段。

仅集团公司“概预算变更情况”栏目缺少事实或调整资料时降为 `INFO`，不阻断生成。脚本以中性文字说明未提供的资料，不把未知当“无变更”，不编造审批；已知事实矛盾和真实金额不一致仍需解决。编制日期、审计事实和其他项目类型的既有要求仍保留。

## 受控说明文字

`narratives` 的键必须是栏目 ID，例如 `miit.governance`。文字可以解释事实、差异原因和制度执行，不得：

- 将示例里的结论当作本项目事实；
- 把“未提供问题”写成“未发现问题”；
- 把预算下达等同于资金到账；
- 把账面支出等同于审计认定支出；
- 省略超支/结余达到阈值的具体原因。

集团公司两处正文的明确例外：“支出明细项及结构总体说明”和“资金结余及原因”可由脚本删除有效批复概预算为零的科目段落；原始费用记录、非零结余/超支原因校验、汇总表和明细表不受影响。有效批复概预算取 `adjusted_budget`，未提供调整额时才取 `budget`；缺失值不等于零。
