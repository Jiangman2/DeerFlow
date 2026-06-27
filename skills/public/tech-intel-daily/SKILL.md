---
name: tech-intel-daily
description: Generate Chinese technology-management intelligence daily reports for CNOOC-style marine oil and gas R&D management. Use for 科技情报日报、科技管理情报跟踪、政策/行业/科研动态采集筛选、日报 HTML 生成、科技管理知识库更新、全文标注与研判分析.
---

# Tech Intel Daily

## Activation Contract

This Skill is designed for DeerFlow native slash activation.

When the user starts a turn with `/tech-intel-daily`, treat this `SKILL.md` as the controlling instruction for the turn. The task is not complete until the pipeline script has run and `daily_report.html` has been presented as a file.

Do not satisfy this Skill with a normal chat answer, generic web search, or Markdown-only summary. Do not call `web_search` to manually collect and summarize articles unless the pipeline script itself fails and you are diagnosing the failure.

## Required Entrypoint

Prefer the step-level pipeline entrypoint so the user can see progress through DeerFlow's visible TodoList:

```bash
python /mnt/skills/public/tech-intel-daily/scripts/run_step.py --step <STEP> --date <YYYY-MM-DD> --output-root /mnt/user-data/outputs/tech-intel-daily
```

Run these steps in order:

1. `initialize`
2. `load_requirements`
3. `collect_candidates`
4. `filter_knowledge_base`
5. `enrich_full_text`
6. `render_daily_report`
7. `finalize`

The original one-shot command remains available only as a fallback when the step-level runner is unavailable:

```bash
python /mnt/skills/public/tech-intel-daily/scripts/run_daily.py --date <YYYY-MM-DD> --output-root /mnt/user-data/outputs/tech-intel-daily
```

Do not satisfy this Skill with ad-hoc web search or a Markdown-only report.

## Visible TodoList Protocol

This Skill must make progress visible to the user when DeerFlow exposes the `write_todos` tool.

Before running the first command, if `write_todos` is available, create this visible TodoList:

1. 确认日期与输出目录
2. 加载 Skill 规则、信源目录、筛选规则和分类体系
3. 运行日报采集与候选文章写入
4. 完成相关性筛选并写入知识库
5. 抓取/补全文本并生成全文标注
6. 渲染唯一交付物 `daily_report.html`
7. 校验并展示 HTML 日报

Keep exactly one item `in_progress` at a time. For every step:

1. Mark the corresponding Todo item `in_progress`.
2. Run the matching `run_step.py --step ...` command.
3. Read the JSON result from stdout.
4. Mark the Todo item `completed` with a short note from the JSON result.
5. Move to the next Todo item.

The mapping is:

- Todo 1 -> `run_step.py --step initialize`
- Todo 2 -> `run_step.py --step load_requirements`
- Todo 3 -> `run_step.py --step collect_candidates`
- Todo 4 -> `run_step.py --step filter_knowledge_base`
- Todo 5 -> `run_step.py --step enrich_full_text`
- Todo 6 -> `run_step.py --step render_daily_report`
- Todo 7 -> `run_step.py --step finalize`, then verify and call `present_files`

If `write_todos` is unavailable because Plan Mode is off, still run the same step-level commands in order. The script embeds an internal execution checklist into `daily_report.html`.

## Date Rules

Determine `<YYYY-MM-DD>` before running the command.

- If the user gives an explicit date, use that date.
- If the user says `昨天` or `昨日`, use the previous calendar day relative to the current date reminder.
- If the user says `前天`, use two calendar days before the current date reminder.
- If the user says `今天` or `今日`, use the current date reminder.
- If the user gives no date, ask one concise clarification question unless the request clearly means today's report.

Filter and report by article publication date, not crawl date.

## Output Contract

The only user-facing artifact is:

```text
/mnt/user-data/outputs/tech-intel-daily/<YYYY-MM-DD>/daily_report.html
```

After the pipeline command finishes:

1. Verify `daily_report.html` exists and is non-empty.
2. Present only `daily_report.html` with `present_files`.
3. Use the command JSON `chat_summary` as the source of truth for the final chat reply.

Do not present or advertise these as separate user-facing artifacts:

- `articles/*.html`
- `daily_report_standalone.html`
- zip packages
- `collection_diagnostics.json`
- `execution_todo.json`
- temporary candidate or knowledge-base sidecars

Diagnostics and execution checklist content must be embedded in `daily_report.html`.

## Failure Rules

If the command returns a non-zero exit code:

- Do not write a substitute report in chat.
- Do not invent article titles, policy names, standards, counts, audit IDs, deadlines, or conclusions.
- Read the exact error output.
- Fix path, dependency, date, or script issues when possible.
- Retry the same entrypoint.
- If it still fails, report the exact blocker and the command that failed.

If no articles are selected, still generate and present `daily_report.html`. The report must explain whether the result came from no candidates, access limitations, date mismatch, or filtering rules.

## Report Content Requirements

`daily_report.html` must contain:

- 日报首页
- 每篇入选文章的原文链接
- 每篇文章的独立研判与分析结论
- 可向下展开的全文标注卡片
- 采集诊断
- 执行步骤清单
- 动态库当前内容，包括关键词库、业务标签库、重点内容标准库

For each selected article:

- Use the article's original URL.
- Distinguish facts from inference.
- Keep the analysis specific to that article; do not reuse the same generic conclusion for all cards.
- If full text is inaccessible, render available evidence passages and state the limitation.

## References

Load supporting references only when needed:

- `references/source-catalog.md`: source list and collection guidance.
- `references/filtering-rules.md`: date, source, and relevance filtering rules.
- `references/taxonomies.md`: keyword library, business tag library, focus taxonomy, and update rules.
- `references/output-templates.md`: report and article-detail formats.

## Final Reply Format

After `present_files` succeeds, reply in Chinese using only facts from `chat_summary` and the generated report.

The final reply should be short:

```text
日报已生成并展示：/mnt/user-data/outputs/tech-intel-daily/<YYYY-MM-DD>/daily_report.html

<paste or lightly format chat_summary>
```

Do not add extra web-search findings, external conclusions, fabricated standards, or unverified "本期重点情报" items.
