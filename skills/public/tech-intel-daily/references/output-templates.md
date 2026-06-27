# Output Templates

## Final HTML Deliverable

The final user-facing artifact must be one HTML file:

- `daily_report.html`: daily index page plus expandable full-text annotation cards.

The index page must include:

- A title with date.
- A visual legend for all highlight types: 国家领导原话、国家政策、国家部署工作或任务、政策导向、潜在资源、潜在机遇、潜在风险、优秀做法/经验.
- A summary section.
- Article sections in reverse chronological order.
- For each article: title, source/date/edition, keywords, business tags, URL, management analysis, relevance score/reasons when available, and an expandable full-text annotation section.
- Each article card should include a collapsed in-page preview using native `<details>` / `<summary>` so users can expand the full-text annotation and analysis directly inside `daily_report.html`.
- Each expandable full-text annotation section must include a `重点内容摘要` section. Keep bullet formatting, and prefix every bullet with the content type, such as `重点任务：...`, `政策导向：...`, `潜在资源：...`.
- Dynamic library update tables.
- Knowledge-base ingestion summary.
- Collection notes and access limitations.
- Embedded collection diagnostics.
- Embedded execution checklist.

Use `scripts/enrich_full_text.py` to fetch and mark accessible full text, then `scripts/render_html_report.py` to render `daily_report.html`.

## HTML Article Card With Inline Detail

```html
<article class="article-card">
  <h2>文章标题</h2>
  <p class="meta">文章来源、日期及版次</p>
  <p>关键词及重点内容：...</p>
  <p>业务标签：...</p>
  <p>文章全文超链接：<a href="...">...</a></p>
  <section class="analysis">研判：...</section>
  <details class="inline-detail">
    <summary>在日报中展开全文标注与分析</summary>
    <section>
      <h3>重点内容摘要</h3>
      <ul>
        <li>政策导向：...</li>
        <li>潜在机遇：...</li>
      </ul>
    </section>
    <section class="article-body">
      <h3>全文标注</h3>
      <p>
      整段原文上下文，其中
      <span class="national-policy">国家政策片段</span>
      <span class="national-task">部署任务片段</span>
      <span class="potential-opportunity">潜在机遇片段</span>
      会在同一段落内被多处标注。
      </p>
      <p>下一段原文继续自然展示，不加编号，不做摘录列表。</p>
    </section>
  </details>
</article>
```

## Working Markdown Draft

```markdown
# 科技管理情报跟踪日报（YYYY年MM月DD日）

## 一、今日要点

1. ...

## 二、入选文章（倒叙）

### 文章标题：XXXXX

文章来源、日期及版次：`《人民日报》（YYYY年MM月DD日 第 NN 版）`

关键词及重点内容：关键词1、关键词2、重点内容1、重点内容2

业务标签：业务大类/专业方向/业务标签；业务标签2

文章全文超链接：[XXXXX](https://example.com)

研判：用1-3句话说明对科技管理、海洋油气科研生产或企业研发治理的意义。

## 三、动态库更新

### 关键词库

| 状态 | 词条 | 证据 | 更新理由 |
| --- | --- | --- | --- |

### 业务标签库

| 状态 | 标签 | 映射方向 | 证据 | 更新理由 |
| --- | --- | --- | --- | --- |

### 重点内容标准库

| 状态 | 标准 | 证据 | 更新理由 |
| --- | --- | --- | --- |

## 四、知识库入库摘要

| 标题 | 来源 | 日期 | 核心标签 | 入库状态 |
| --- | --- | --- | --- | --- |

## 五、采集说明

- 已覆盖信源：
- 未覆盖或访问受限信源：
- 需人工复核事项：
```

## JSONL Article Record For HTML Rendering

Use one compact JSON object per selected article:

```json
{
  "date": "YYYY-MM-DD",
  "title": "文章标题",
  "source": "人民日报",
  "source_type": "资讯类信息 | 政策及政策解读类信息 | 领导讲话/批示指示类信息",
  "edition_or_section": "第 01 版",
  "url": "https://example.com",
  "keywords": ["关键词1", "关键词2"],
  "business_tags": ["科技项目/数智化/AI大模型"],
  "focus_types": ["国家政策", "政策导向"],
  "summary": "100字以内摘要",
  "management_implications": ["对科技管理的启示1", "启示2"],
  "evidence_passages": [
    {
      "type": "政策导向",
      "context": "整段上下文原文。上下文中包含一处或多处需要重点标注的原文片段。",
      "text": "需要标注的原文片段",
      "marks": [
        {
          "type": "政策导向",
          "text": "需要标注的原文片段"
        }
      ],
      "note": "为什么这样标注"
    }
  ],
  "full_text_paragraphs": [
    {
      "context": "全文段落原文。",
      "marks": [
        {
          "type": "国家部署工作或任务",
          "text": "段落中需要标注的原文片段"
        },
        {
          "type": "潜在机遇",
          "text": "另一处需要标注的原文片段"
        }
      ]
    }
  ],
  "collected_at": "YYYY-MM-DDTHH:MM:SS"
}
```

## Taxonomy Update Record

```json
{
  "term": "新词条",
  "category": "keyword | business_tag | focus_standard",
  "status": "new | reinforced",
  "evidence_title": "文章标题",
  "evidence_url": "https://example.com",
  "first_seen": "YYYY-MM-DD",
  "reason": "为什么需要加入或强化"
}
```
