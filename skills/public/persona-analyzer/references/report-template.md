# 输出报告模板

本文件定义了 Persona-Analyzer 的完整输出报告模板。当会话完成或暂停时，按照此模板生成最终报告。

---

## 完整报告模板

```markdown
# {job_title} — 业务流程与痛点分析报告

*生成时间：{datetime}*

## 一、岗位定位

| 项目 | 内容 |
|------|------|
| 岗位名称 | {job_title} |
| 业务归属 | {domain} |
| 工作地点 | {location} |
| 常用工具 | {tools} |

**一句话描述：** {summary}

## 二、主要业务内容

{work_description}

## 三、业务流程

[SVG flowchart rendered via dynamic-ui]

```
{mermaid_source_code}
```

## 四、痛点分析

### 4.1 痛点总览

| 痛点类型 | 数量 | 占比 |
|---------|------|------|
| 🔧 技术痛点 | {tech_count} | {tech_pct} |
| 📋 流程痛点 | {process_count} | {process_pct} |
| 📊 数据痛点 | {data_count} | {data_pct} |
| 🤝 协同痛点 | {collab_count} | {collab_pct} |
| 🏛 管理痛点 | {mgmt_count} | {mgmt_pct} |
| **合计** | **{total}** | **100%** |

### 4.2 痛点优先级矩阵

[Priority matrix visualization]

| 优先级象限 | 痛点数量 | 典型痛点 |
|-----------|---------|---------|
| 🟢 Quick Wins（高影响+易解决） | {qw_count} | {qw_examples} |
| 🟡 重点攻克（高影响+中等难度） | {focus_count} | {focus_examples} |
| 🔴 长期规划（高影响+难解决） | {long_count} | {long_examples} |
| ⚪ 其他（中低影响） | {other_count} | — |

### 4.3 系统性痛点关联分析

{systemic_analysis}

> 系统性痛点是指同一类型的痛点横跨多个业务节点出现，表明问题根源不在单一环节，而是系统性的架构或机制问题。

### 4.4 逐节点痛点详情

#### 节点 {n}: {node_name}

**参与方式：** {participation_mode}

**痛点列表：**

| 序号 | 痛点描述 | 类型 | 影响 | 频率 | 当前做法 | 理想状态 |
|-----|---------|------|------|------|---------|---------|
| 1 | {description} | {type} | {impact} | {frequency} | {current_approach} | {ideal_state} |

**根因分析（5 Why）：** *(仅对应用了5 Why追问的痛点)*
> {root_cause_chain}

---

*(重复以上格式，为每个节点生成详情)*

## 五、关键发现与建议

### 5.1 关键发现

{key_findings}

### 5.2 Quick Wins（优先处理）

以下痛点影响大、解决难度低，建议优先处理：

| 优先级 | 建议 | 针对节点 | 预期效果 |
|-------|------|---------|---------|
| 高 | {suggestion} | {target_node} | {expected_outcome} |

### 5.3 中长期改进建议

{medium_long_term_recommendations}

### 5.4 平台优化方向

{platform_optimization_directions}

---
📌 **进度标识**
岗位：{job_title}
已完成阶段：{completed_stages}
已完成节点：{completed_nodes}
剩余节点：{remaining_nodes}
下次续接：从【{next_node}】继续
```

## 中期报告模板（暂停时使用）

当用户选择暂停而非完成时，使用以下精简模板：

```markdown
# {job_title} — 中期分析报告

*生成时间：{datetime}* | *状态：暂未完成*

## 已完成内容

✅ 岗位定位：{job_title} — {domain}
✅ 业务流程：{completed_nodes_count}个节点已梳理
✅ 痛点分析：{analyzed_nodes_count}个节点已完成

## 已发现痛点

| 痛点类型 | 已发现数量 |
|---------|-----------|
| 🔧 技术痛点 | {tech_count} |
| 📋 流程痛点 | {process_count} |
| 📊 数据痛点 | {data_count} |
| 🤝 协同痛点 | {collab_count} |
| 🏛 管理痛点 | {mgmt_count} |

## 已发现系统性模式

{systemic_analysis_preview}

---
📌 **进度标识**
岗位：{job_title}
✅ 已完成阶段：{completed_stages}
✅ 已完成节点：{completed_nodes}
⏳ 剩余节点：{remaining_nodes}
🔜 下次续接：从【{next_node}】开始继续挖掘痛点
```