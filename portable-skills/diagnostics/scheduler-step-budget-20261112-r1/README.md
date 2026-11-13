# Local scheduler super-step reproduction

This is an offline configuration fixture, **not a verified production compiled
graph** and not a production change or business-acceptance receipt.

`reproduce.py` parses source hook declarations using AST and compiles no-op
stand-ins with the installed LangChain agent factory. A fake model requests
16 distinct fake `bash` calls, then a final answer. The fake tool never executes
a shell command. Real middleware bodies, application startup, Sandbox providers,
models, databases and external services are not invoked.
The reproduction also disables inherited optional remote tracing. The prior
inline run did not instrument socket activity; its evidence is the fake local
execution path, not an independently measured network counter.

The tested versions are LangChain 1.3.14, langchain-core 1.4.9 and LangGraph
1.2.9. The fixture has 13 before-agent hooks, 3 before-model hooks, 13 after-model
hooks and 8 after-agent hooks. Summarization is omitted; the listed optional
middleware hooks are included. Those choices have not been established as the
live task's configuration.

The prior inline execution produced the results preserved in `RESULT.json`:

| Budget | Outcome | Model nodes | Tools nodes | Total nodes |
| --- | --- | ---: | ---: | ---: |
| 300 | Recursion cap after the 16th model proposal, before its tool | 16 | 15 | 300 |
| 1000 | Successful synthetic completion | 17 | 16 | 326 |

These results demonstrate that a small number of normal tool rounds can exhaust
a super-step budget when middleware hooks are separate graph nodes. They do not
prove the full cause of any named production task or justify an unbounded budget.

To reproduce with the pinned dependencies from the repository root:

```text
python -I -B portable-skills/diagnostics/scheduler-step-budget-20261112-r1/reproduce.py
```

The script writes nothing and fails if the dependency versions or hook-count
fixture change. The stored JSON was transcribed from the prior successful run;
creating this directory did not rerun it. Backend defaults and production
configuration remain unchanged.
