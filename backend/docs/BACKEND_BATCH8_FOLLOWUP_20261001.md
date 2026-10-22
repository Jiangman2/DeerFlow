# Batch-eight follow-up: fixture capability scalars and identity scope

The first real-dependency batch-eight focused run returned 264 passed and
5 failed. The local-sandbox and read-range fixes passed their focused coverage.
This small package changes tests and documentation only; no worker, batching,
guard middleware, prompt template, runtime credential, or deployment is changed.

## Identity assertion

The current role identifies the product as `海能通用智能体`. The same
system prompt legitimately names DeerFlow in internal native-tool documentation:
`read_file` is a tool call, not a command to run in the Sandbox shell.
The old global substring ban therefore rejected correct internal documentation.

Scope the identity assertions to the explicit role block and keep the repository
URL ban plus both no-inferred-artifact-branding assertions. Add positive coverage
for the native `read_file` invocation warning; do not delete that runtime guidance.
This does not authorize framework branding of user-facing answers or artifacts.

## Stop-reason integration fixture

Four unchanged integration tests created an unrestricted AsyncMock bridge. Its
invented `max_publish_batch_size` was itself an AsyncMock, so the real
MessagePublishBatcher failed at `max(1, max_events)` before DummyAgent captured
the runtime. Those errors do not establish that a middleware lost stop_reason.

Construct the test bridge with the real StreamBridge interface and explicit
scalar capabilities: batching disabled and maximum batch size 64. Keep the
existing publish/end/cleanup mocks, real guard execution, captured-runtime
identity checks, run success checks, and all four exact persisted stop reasons.
No test is deleted, skipped, or redefined as an expected failure. Production
batching and its error propagation are not weakened to accommodate a fake.

## Validation

Local fixtures evaluate the actual prompt template and assertion body, rejecting
an injected legacy role and missing no-branding guard while accepting technical
framework documentation. They exercise the real batcher with the repaired fake
bridge, verify scalar capability types and ordered publication, and check that
each of the four integration test bodies changes only bridge construction.
The package is checked in a disposable merge/index fixture, including guard
drift rejection and forward/reverse application. Real worker/middleware
integration must still run on dmdb with its complete dependencies.

Re-run the full batch-eight focused set before continuing the full backend
suite. A focused pass does not establish release readiness. Existing deferrals
and the requirement to finish all tests before final merge/deploy still apply.
