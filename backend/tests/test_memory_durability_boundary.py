from deerflow.agents.memory.backends.deermem.deermem.core.updater import (
    _normalize_memory_update_fact,
)


def test_one_turn_operational_instruction_is_not_durable_memory() -> None:
    fact = _normalize_memory_update_fact(
        {
            "content": ("User explicitly instructs the agent to avoid network access during task execution."),
            "category": "preference",
            "confidence": 0.99,
        }
    )

    assert fact is None


def test_direct_preference_statement_remains_eligible() -> None:
    fact = _normalize_memory_update_fact(
        {
            "content": "User prefers concise Chinese answers.",
            "category": "preference",
            "confidence": 0.99,
        }
    )

    assert fact is not None
    assert fact["content"] == "User prefers concise Chinese answers."


def test_behavior_requires_repeated_pattern_evidence() -> None:
    one_off = _normalize_memory_update_fact(
        {
            "content": "User uses academic-research for this analysis.",
            "category": "behavior",
            "confidence": 0.9,
        }
    )
    repeated = _normalize_memory_update_fact(
        {
            "content": "User regularly validates deployments with a browser smoke test.",
            "category": "behavior",
            "confidence": 0.9,
        }
    )

    assert one_off is None
    assert repeated is not None
