"""Actual sync/async guard outcomes; neutral contract has no observer coupling."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphBubbleUp

from deerflow.authz.outcome import pop_authorization_outcome
from deerflow.guardrails.middleware import GuardrailMiddleware
from deerflow.guardrails.provider import GuardrailDecision, GuardrailReason


class Provider:
    name = "policy"
    policy_id = "operator-policy"
    policy_version = "v3"

    def __init__(self, outcome):
        self.outcome = outcome
        self.declarations = 0

    def release_policy_parameters(self):
        self.declarations += 1
        return {"rules": ["safe"]}

    def evaluate(self, request):
        if self.outcome == "error":
            raise RuntimeError("outage")
        if self.outcome == "interrupt":
            raise GraphBubbleUp()
        return GuardrailDecision(allow=self.outcome == "allow", policy_id="matched-rule", reasons=[GuardrailReason(code="rule")])

    async def aevaluate(self, request):
        return self.evaluate(request)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("outcome,fail_closed,expected", [("allow", True, "allowed"), ("deny", True, "denied"), ("error", True, "denied"), ("error", False, "allowed")])
def test_actual_decision_handoff_before_tool_or_denial(asynchronous, outcome, fail_closed, expected):
    provider = Provider(outcome)
    guard = GuardrailMiddleware(provider, fail_closed=fail_closed)
    context = {}
    request = SimpleNamespace(tool_call={"name": "safe", "id": "call", "args": {}}, runtime=SimpleNamespace(context=context))
    captured = []

    def handler(request):
        captured.append(pop_authorization_outcome(context, "call"))
        return "executed"

    async def ahandler(request):
        return handler(request)

    result = asyncio.run(guard.awrap_tool_call(request, ahandler)) if asynchronous else guard.wrap_tool_call(request, handler)
    if expected == "allowed":
        assert result == "executed"
        decision = captured[0]
    else:
        assert not captured
        assert result.status == "error"
        decision = pop_authorization_outcome(context, "call")
    assert decision.decision == expected
    assert decision.policy_id == ("operator-policy" if outcome == "error" else "matched-rule")
    assert decision.policy_version == "v3"
    assert decision.reason_codes == (("oap.evaluator_error",) if outcome == "error" else ("rule",))
    assert pop_authorization_outcome(context, "call") is None
    assert provider.declarations == 0
    declaration = guard.release_policy_parameters()
    assert declaration["policy"] == {"id": "operator-policy", "version": "v3"}
    assert declaration["provider_parameters"] == {"rules": ["safe"]}
    assert provider.declarations == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_control_flow_is_not_a_policy_decision(asynchronous):
    context = {}
    request = SimpleNamespace(tool_call={"name": "safe", "id": "call", "args": {}}, runtime=SimpleNamespace(context=context))
    guard = GuardrailMiddleware(Provider("interrupt"))
    handler = AsyncMock() if asynchronous else MagicMock()
    with pytest.raises(GraphBubbleUp):
        if asynchronous:
            asyncio.run(guard.awrap_tool_call(request, handler))
        else:
            guard.wrap_tool_call(request, handler)
    handler.assert_not_called()
    assert pop_authorization_outcome(context, "call") is None


def test_later_explicit_guard_denial_is_authoritative():
    outer = GuardrailMiddleware(Provider("allow"), name="AuthorizationGuardrailMiddleware")
    inner = GuardrailMiddleware(Provider("deny"))
    context = {}
    request = SimpleNamespace(tool_call={"name": "safe", "id": "call", "args": {}}, runtime=SimpleNamespace(context=context))
    handler = MagicMock()
    result = outer.wrap_tool_call(request, lambda req: inner.wrap_tool_call(req, handler))
    assert result.status == "error"
    handler.assert_not_called()
    assert pop_authorization_outcome(context, "call").decision == "denied"
