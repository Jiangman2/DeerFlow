---
name: academic-research
description: "Academic Research Skills package for /ars-* commands, academic research, literature review, paper writing, manuscript review, citation checking, rebuttal audit, and full research-to-publication orchestration."
license: "CC BY-NC 4.0; source project: https://github.com/Imbad0202/academic-research-skills"
metadata:
  version: "2.0.0"
  source: "https://github.com/Imbad0202/academic-research-skills"
  runtime_package: "/mnt/skills/third_party/academic-research-skills"
---

# Academic Research Skills

This is the single platform-visible entry point for the Claude Code-native Academic Research Skills package.

The original package is preserved under `/mnt/skills/third_party/academic-research-skills`. Its `commands/`, `.claude/`, `agents/`, `references/`, `scripts/`, `templates/`, and component skill directories remain runtime resources. They are not separate platform skills.

## Runtime Contract

When this skill is activated, treat it as a compatibility layer for Claude Code ARS:

- User-facing entry: `academic-research`
- Claude Code command entries: `/ars-*`
- Internal component skills: `deep-research`, `academic-paper`, `academic-paper-reviewer`, `academic-pipeline`
- Runtime package: `/mnt/skills/third_party/academic-research-skills`
- Command registry: `/mnt/skills/third_party/academic-research-skills/commands/*.md`
- Routing source of truth: `/mnt/skills/third_party/academic-research-skills/MODE_REGISTRY.md`
- Runtime rules: `/mnt/skills/third_party/academic-research-skills/.claude/CLAUDE.md`

| User intent | Use |
| --- | --- |
| Find papers, build a literature map, fact-check claims, produce a research brief, run a systematic review, or clarify a research topic | `deep-research` |
| Write a manuscript, outline a paper, revise a draft, convert format, check citations, write an abstract, prepare disclosure, or audit a rebuttal | `academic-paper` |
| Review a manuscript, simulate peer review, check methodology, calibrate reviewer judgment, or verify a revised paper | `academic-paper-reviewer` |
| Run the full research-to-publication workflow across research, writing, integrity checks, review, revision, and finalization | `academic-pipeline` |

## Routing Rules

If the user invokes `/ars-*`, follow the command registry exactly.

If the user asks for a complete project from research topic to final paper, route to `academic-pipeline`.

If the user asks for research before writing, route to `deep-research`.

If the user already has research materials and wants a paper, route to `academic-paper`.

If the user already has a draft and wants critique, route to `academic-paper-reviewer`.

If the user's request is ambiguous, ask one short clarifying question with these options: research only, write a paper, review a draft, or full pipeline.

## Execution Rules

After choosing a route, explicitly tell the user which command, component skill, and mode were selected. Then follow the component skill's `SKILL.md`, relevant agents, references, scripts, and templates from the preserved runtime package.

For full or long workflows, do not claim completion after a single intermediate artifact. Surface checkpoints, list generated artifact filenames, and ask for confirmation before proceeding past mandatory checkpoint boundaries.
