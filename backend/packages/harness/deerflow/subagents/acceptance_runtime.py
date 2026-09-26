"""Parent-owned checklist IO, isolated from child and concurrent parent leases."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.sandbox.lease import SANDBOX_SERVER_OWNED_CONTEXT_KEYS, acquire_sandbox_client_lease, run_sync_lifecycle_operation
from deerflow.subagents.acceptance_checks import AcceptanceVerdict, _resolve_scoped_path, check_acceptance_criteria, parse_file_criterion
from deerflow.subagents.report_contract import normalize_acceptance_criteria

logger = logging.getLogger(__name__)


def _without_file_io(criteria: list[str], thread_data: Mapping[str, Any] | None, evidence: list[dict[str, Any]] | None) -> AcceptanceVerdict | None:
    # A denied/unavailable file check is unknown, not a nonexistent file or a
    # failed test. Recorded command evidence remains independently checkable.
    return check_acceptance_criteria(
        criteria,
        thread_data=thread_data,
        bash_executions=evidence,
        size_prober=lambda *_: None,
        readable_prober=lambda *_: None,
        content_reader=lambda *_: "Error: File evidence unavailable",
    )


async def check_task_acceptance(
    criteria: list[str] | None,
    *,
    runtime: Any,
    thread_data: Mapping[str, Any] | None,
    bash_executions: list[dict[str, Any]] | None,
) -> AcceptanceVerdict | None:
    """Check completed child evidence without allowing IO to outlive its hold.

    A checklist is not an execution-status rewrite or an automatic test rerun.
    Only scoped file criteria acquire a fresh *holder* (not necessarily a new
    Sandbox); command-only/undecidable criteria do not initialize a provider.
    The private runtime leaves parent context/state and its shell scope intact.
    """
    normalized = normalize_acceptance_criteria(criteria)
    if not normalized:
        return None
    needs_file_io = any((file_criterion := parse_file_criterion(item)) is not None and _resolve_scoped_path(file_criterion[1], thread_data) is not None for item in normalized)
    if not needs_file_io:
        return _without_file_io(normalized, thread_data, bash_executions)

    from deerflow.sandbox.sandbox_provider import get_sandbox_provider
    from deerflow.sandbox.tools import _check_employee_execution, _validate_employee_sandbox, sandbox_authorization_scope_async

    context = dict(getattr(runtime, "context", None) or {})
    config = dict(getattr(runtime, "config", None) or {})
    thread_id = context.get("thread_id") or config.get("configurable", {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return _without_file_io(normalized, thread_data, bash_executions)
    for key in SANDBOX_SERVER_OWNED_CONTEXT_KEYS | {"sandbox_id"}:
        context.pop(key, None)
    context.update(thread_id=thread_id, user_id=resolve_runtime_user_id(runtime))
    private_runtime = SimpleNamespace(context=context, config=config, state={"thread_data": dict(thread_data or {})})
    lease = None
    try:
        async with sandbox_authorization_scope_async(private_runtime):
            # Preserve Square grant revocation before any provider acquisition,
            # then validate the acquired mount before even local stat/open.
            await run_sync_lifecycle_operation(_check_employee_execution, private_runtime)
            provider = await run_sync_lifecycle_operation(get_sandbox_provider)
            lease = await acquire_sandbox_client_lease(provider, thread_id, user_id=context["user_id"], owner_prefix="acceptance")
            if lease.sandbox is None:
                raise RuntimeError("Acceptance sandbox client unavailable")
            context.update(sandbox_id=lease.sandbox_id, sandbox_lease_owner_id=lease.owner_id)
            private_runtime.state["sandbox"] = {"sandbox_id": lease.sandbox_id}
            await lease.run_sync(_validate_employee_sandbox, provider, lease.sandbox_id, private_runtime)
            return await lease.run_sync(check_acceptance_criteria, normalized, runtime=private_runtime, thread_data=thread_data, bash_executions=bash_executions)
    except Exception as exc:
        # Provider/policy error text can contain credentials. Cancellation is a
        # BaseException and must propagate after the worker and holder drain.
        logger.warning("Acceptance file evidence unavailable (%s)", type(exc).__name__)
        return _without_file_io(normalized, thread_data, bash_executions)
    finally:
        if lease is not None:
            await lease.release()
