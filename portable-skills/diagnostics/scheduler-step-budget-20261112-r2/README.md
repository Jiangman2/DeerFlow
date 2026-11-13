# Exact-version offline scheduler fixture

An already-cached ARM64 Gateway image (`c1b667d7382b`) contains the exact
libraries reported by production: LangChain 1.2.15, langchain-core 1.3.3 and
LangGraph 1.1.9. The prior reproduction's newer dependency versions are no
longer a limitation of this additional experiment. No packages were downloaded
and no local virtual environment was changed.

The frozen r1 topology fixture was executed in that cached image with
`--pull never --platform linux/arm64 --network none`, read-only repository
mounts and Python as the overridden entry point. Application startup and real
middleware bodies were not invoked. The fake model and fake tool produced
the same results: budget 300 stopped after 16 model nodes and 15 tools nodes;
budget 1000 completed 17 model nodes and 16 tools nodes in 326 total nodes.
`RESULT.json` transcribes that completed execution without repeating it.

This remains a **synthetic enabled-middleware configuration**, with 13
before-agent, 3 before-model, 13 after-model and 8 after-agent hooks. It is not
proof of the production task's compiled graph or its enabled optional hooks.
Neither the r1 evidence nor the frozen 2.0.3 Skill candidate was modified.

`reproduce.py` reuses r1's source-declaration-only topology fixture while
checking the exact older dependency versions. Run it only inside the cached
environment, with the repository mounted read-only at `/src`:

```text
/app/backend/.venv/bin/python -I -B /src/portable-skills/diagnostics/scheduler-step-budget-20261112-r2/reproduce.py
```

Source reconciliation against the supplied production hashes is recorded in
`SOURCE-MATCH.json`. In particular, neither the current nor the OLD local
`app/gateway/app.py` matches the production file. A matching scheduler schema
does not establish application-startup source identity or authorize replacing
that file. No production configuration was changed.
