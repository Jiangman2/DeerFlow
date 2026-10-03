---
name: persona-analyzer
description: Conduct a structured but conversational job-role, business-process, and pain-point interview for CNOOC exploration and production work, then deliver a thread-scoped report. Use when the user explicitly asks to map their role/workflow, identify operational pain points, or create a persona-analysis report. Do not use for a one-off business question, general document summary, or production-data query.
version: 1.0.0
---

# Persona Analyzer

Use this Skill to help a user understand their role, end-to-end work, and
improvement opportunities. Treat it as a collaborative interview, not an HR
form. The result is an evidence-based report built only from information the
user provides or explicitly confirms.

## Boundaries and data handling

- Ask only for information needed to map the work. Do not request personal
  contact details, credentials, customer data, production-system exports, or
  other unnecessary sensitive information.
- Make clear that the session record stays in this thread's controlled
  workspace. Use a role title or alias in filenames; do not use a person's
  name unless the user explicitly asks for it.
- Do not infer organizational facts, pain-point severity, or root causes as
  facts. Label hypotheses and ask the user to confirm them.
- Never write outside the current thread's `/mnt/user-data/workspace` and
  `/mnt/user-data/outputs` roots. Do not silently overwrite a prior session.

## Workspace contract

At the start, create or reuse exactly one analysis ID in this thread:

```text
/mnt/user-data/workspace/persona-analyzer/sessions/<analysis_id>.json
/mnt/user-data/workspace/persona-analyzer/diagrams/<analysis_id>-flow.svg
/mnt/user-data/outputs/persona-analyzer/<analysis_id>/
```

After each completed stage, update the session JSON through the normal file
tools and read it back to confirm the persisted evidence. The session JSON follows
`references/output-schema.json`; preserve unknown fields from a prior record.
Only state that the work is resumable after the write succeeds. When the user
says `继续`, first look for the active thread's most recent session record and
resume its `progress.next_resume_point`; ask for an analysis ID only if more
than one plausible record exists.

Read the relevant references before the action they govern:

- Stage 1 tags: `references/persona-tags.md`; read
  `references/persona-profiles.md` only when a role does not map cleanly.
- Stage 2 process mapping: `references/business-processes.md`.
- Stage 3 interviewing and fatigue handling:
  `references/interview-techniques.md`.
- Final report: `references/report-template.md`.

## Conversation flow

### 1. Establish scope and role

For a new session, briefly explain that the interview can pause and resume.
Collect only the role title, business domain, work setting, and frequently used
tools. Let a user provide these in one message; do not ask already answered
questions. Show a short role card and invite corrections.

Map the confirmed information to the seven tag dimensions in the background.
Keep tags in the session JSON but do not expose raw tag codes unless the user
asks for them.

### 2. Map the work

Ask the user to describe a typical day or a recent representative task. Extract
the sequence, ownership, inputs, outputs, collaborators, and decision points.
Use the domain process reference to normalize familiar nodes, but preserve the
user's wording and mark unmatched nodes as custom.

Create a self-contained SVG flowchart in the diagram path. It must be a static
artifact: no `dynamic-ui`, `PureShowWidget`, remote scripts, or browser-hosted
content. For 3–5 nodes prefer a horizontal layout; split longer flows into
readable phases. Distinguish lead, participant, and support/approval roles by
color and include a small legend. Keep Mermaid source in the JSON record as a
portable source representation.

Present the SVG only after it was written successfully, then ask the user to
correct missing or misordered steps.

### 3. Identify and prioritize pain points

For each user-confirmed node, use the framing-first four-question method from
the interview reference. Ask naturally, one topic at a time; do not read a
questionnaire aloud. Categorize confirmed pain points as technical, process,
data, collaboration, or management.

Use a 5-Why exploration only for a high-impact point and only after the user
agrees. Treat the resulting root cause as a user-tested hypothesis, not a
diagnosis. After every two nodes, use the fatigue rules in the reference. If
the user signals a pause, stop questioning, save the intermediate JSON, and
produce an interim report.

For confirmed pain points, capture impact and difficulty, build a 2×2
priority matrix in the report, and identify a cross-node pattern only when the
same category occurs in at least three nodes.

## Deliverables

For a completed or paused session, read the report template and write:

1. a Markdown report;
2. a static HTML report with no executable script; embed a sanitized inline SVG
   or link to the separately emitted SVG by a relative file name; and
3. the final session JSON.

Place copies intended for the user under the output path, then call
`present_files` for the Markdown, HTML, SVG, and JSON only after all files are
successfully written. Clearly identify an interim report as incomplete.

Generate Excel only when the user explicitly requests it. First ensure the
session JSON is valid and then run:

```text
python <SKILL_DIR>/scripts/export-to-excel.py <session-json> -o <output-xlsx>
```

The script requires `openpyxl`, which must be present in the Sandbox image. Do
not install packages during a user conversation. If it is unavailable, deliver
the Markdown, HTML, SVG, and JSON and report the image dependency gap.

## Completion quality bar

Before final delivery, verify that every claim in the report comes from a user
statement or is explicitly marked as a hypothesis; that the report links to
the correct SVG; that the JSON contains its stage/progress fields; and that all
reported files exist in the thread output directory. Do not substitute a plan
for the report or claim an artifact was saved without a successful write
receipt.
