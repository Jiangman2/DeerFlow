"""Official #4365 delivery callback contracts, adapted to enterprise runtime."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


@pytest.fixture
def journal_setup():
    store = MemoryRunEventStore()
    return RunJournal("r1", "t1", store, flush_threshold=100), store


class TestDeliveryTracking:
    """Slice 1 (#4272): journal records artifact production for run.delivery."""

    @staticmethod
    def _register_tool_call(j: RunJournal, tool_call_id: str, name: str) -> None:
        from langchain_core.messages import AIMessage

        ai = AIMessage(content="", tool_calls=[{"id": tool_call_id, "name": name, "args": {}}])
        j._remember_current_run_tool_calls(ai, caller="lead_agent")

    def test_callbacks_run_inline_to_serialize_parallel_mutations(self, journal_setup):
        j, _ = journal_setup

        # LangChain dispatches synchronous handlers with run_inline=False via
        # run_in_executor, allowing parallel tool callbacks to mutate one
        # journal from different threads.
        assert j.run_inline is True

    @pytest.mark.anyio
    async def test_concurrent_callbacks_on_one_journal_are_serialized(self, journal_setup):
        from langchain_core.callbacks.manager import ahandle_event
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, _ = journal_setup
        commands = []
        for index, path in enumerate(("report.md", "report.md", "appendix.md"), start=1):
            tool_call_id = f"call_{index}"
            self._register_tool_call(j, tool_call_id, "present_files")
            commands.append(
                Command(
                    update={
                        "artifacts": [f"/mnt/user-data/outputs/{path}"],
                        "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
                    }
                )
            )

        # This is the real LangChain async callback dispatcher. Because the
        # journal is run_inline, each synchronous mutation completes on the
        # event-loop thread instead of racing in executor threads.
        await asyncio.gather(
            *(
                ahandle_event(
                    [j],
                    "on_tool_end",
                    "ignore_agent",
                    command,
                    run_id=uuid4(),
                )
                for command in commands
            )
        )

        content = j.get_delivery_content()
        assert content["presented"] == 2
        assert set(content["paths"]) == {
            "/mnt/user-data/outputs/report.md",
            "/mnt/user-data/outputs/appendix.md",
        }
        assert set(content["by_tool"]["present_files"]) == set(content["paths"])

    @pytest.mark.anyio
    async def test_concurrent_runs_keep_delivery_accumulators_isolated(self):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        store = MemoryRunEventStore()
        journals = [RunJournal(run_id, "t1", store, flush_threshold=100) for run_id in ("r1", "r2")]

        async def finish_run(journal: RunJournal, index: int) -> None:
            tool_call_id = f"call_run_{index}"
            self._register_tool_call(journal, tool_call_id, "present_files")
            journal.on_tool_end(
                Command(
                    update={
                        "artifacts": [f"/mnt/user-data/outputs/report-{index}.md"],
                        "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
                    }
                ),
                run_id=uuid4(),
            )
            await asyncio.sleep(0)
            journal.record_delivery()
            await journal.flush()

        await asyncio.gather(*(finish_run(journal, index) for index, journal in enumerate(journals, start=1)))

        for index in (1, 2):
            events = await store.list_events("t1", f"r{index}")
            content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
            assert content == {
                "presented": 1,
                "paths": [f"/mnt/user-data/outputs/report-{index}.md"],
                "by_tool": {"present_files": [f"/mnt/user-data/outputs/report-{index}.md"]},
            }

    @pytest.mark.anyio
    async def test_present_files_success_command_recorded_with_attribution(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_1", "present_files")
        cmd = Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/report.md"],
                "messages": [ToolMessage("Successfully presented files", tool_call_id="call_1")],
            }
        )
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        delivery = [e for e in events if e["event_type"] == "run.delivery"]
        assert len(delivery) == 1
        content = delivery[0]["content"]
        assert content["presented"] == 1
        assert content["paths"] == ["/mnt/user-data/outputs/report.md"]
        assert content["by_tool"] == {"present_files": ["/mnt/user-data/outputs/report.md"]}
        assert delivery[0]["category"] == "outputs"

    @pytest.mark.anyio
    async def test_command_with_multiple_messages_records_artifacts_once(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_multi", "present_files")
        cmd = Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/report.md"],
                "messages": [
                    ToolMessage("Successfully presented files", tool_call_id="call_multi"),
                    HumanMessage("Additional command message"),
                ],
            }
        )
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
        assert content == {
            "presented": 1,
            "paths": ["/mnt/user-data/outputs/report.md"],
            "by_tool": {"present_files": ["/mnt/user-data/outputs/report.md"]},
        }

    @pytest.mark.anyio
    async def test_command_with_multiple_tool_names_leaves_artifacts_unattributed(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_present", "present_files")
        self._register_tool_call(j, "call_browser", "browser_screenshot")
        cmd = Command(
            update={
                "artifacts": [
                    "/mnt/user-data/outputs/report.md",
                    "/mnt/user-data/outputs/shot.png",
                ],
                "messages": [
                    ToolMessage("Successfully presented files", tool_call_id="call_present"),
                    ToolMessage("Saved browser screenshot", tool_call_id="call_browser"),
                ],
            }
        )
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
        assert content == {
            "presented": 2,
            "paths": [
                "/mnt/user-data/outputs/report.md",
                "/mnt/user-data/outputs/shot.png",
            ],
            "by_tool": {},
        }

    @pytest.mark.anyio
    async def test_error_command_without_artifacts_not_recorded(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_2", "present_files")
        cmd = Command(update={"messages": [ToolMessage("Error: Only files in /mnt/user-data/outputs can be presented", tool_call_id="call_2")]})
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        delivery = [e for e in events if e["event_type"] == "run.delivery"]
        assert len(delivery) == 1
        assert delivery[0]["content"] == {"presented": 0, "paths": [], "by_tool": {}}

    @pytest.mark.anyio
    async def test_browser_tool_artifacts_recorded_under_producing_tool(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_3", "browser_screenshot")
        cmd = Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/shot.png"],
                "messages": [ToolMessage("Saved browser screenshot", tool_call_id="call_3")],
            }
        )
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
        assert content["presented"] == 1
        assert content["by_tool"] == {"browser_screenshot": ["/mnt/user-data/outputs/shot.png"]}

    @pytest.mark.anyio
    async def test_duplicate_path_tool_pair_recorded_once(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        self._register_tool_call(j, "call_4", "present_files")
        for _ in range(2):
            j.on_tool_end(
                Command(
                    update={
                        "artifacts": ["/mnt/user-data/outputs/report.md"],
                        "messages": [ToolMessage("Successfully presented files", tool_call_id="call_4")],
                    }
                ),
                run_id=uuid4(),
            )
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
        assert content["presented"] == 1
        assert content["paths"] == ["/mnt/user-data/outputs/report.md"]

    @pytest.mark.anyio
    async def test_unattributed_artifacts_counted_without_by_tool_entry(self, journal_setup):
        from langchain_core.messages import ToolMessage
        from langgraph.types import Command

        j, store = journal_setup
        # No _register_tool_call: attribution missing (e.g. tool_call names map miss).
        cmd = Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/anon.txt"],
                "messages": [ToolMessage("ok", tool_call_id="call_unknown")],
            }
        )
        j.on_tool_end(cmd, run_id=uuid4())
        j.record_delivery()
        await j.flush()

        events = await store.list_events("t1", "r1")
        content = next(e for e in events if e["event_type"] == "run.delivery")["content"]
        assert content["presented"] == 1
        assert content["paths"] == ["/mnt/user-data/outputs/anon.txt"]
        assert content["by_tool"] == {}


@pytest.mark.parametrize("tool", ["present_files", "generate_image", "generate_video"])
def test_actual_callback_attribution_without_lead_agent_call(tool):
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    tool_run = uuid4()
    # Subagent / enterprise media tools need not appear in the lead AI message.
    journal.on_tool_start({"name": tool}, "{}", run_id=tool_run, tags=["subagent:worker"])
    journal.on_tool_end(Command(update={"artifacts": ["/mnt/user-data/outputs/result.png"]}), run_id=tool_run)
    assert journal.get_delivery_content()["by_tool"] == {tool: ["/mnt/user-data/outputs/result.png"]}
    assert journal._active_tool_names == {}


def test_callback_identity_wins_over_ambiguous_command_messages():
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    journal._remember_current_run_tool_calls(AIMessage(content="", tool_calls=[{"id": "call", "name": "present_files", "args": {}}]), caller="lead_agent")
    tool_run = uuid4()
    journal.on_tool_start({"name": "generate_image"}, "{}", run_id=tool_run)
    journal.on_tool_end(
        Command(
            update={
                "artifacts": ["/mnt/user-data/outputs/image.png"],
                "messages": [ToolMessage("ok", tool_call_id="call")],
            }
        ),
        run_id=tool_run,
    )
    assert journal.get_delivery_content()["by_tool"] == {"generate_image": ["/mnt/user-data/outputs/image.png"]}


def test_model_prose_and_stale_final_graph_artifacts_are_not_tool_receipts():
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    journal.on_chain_end({"artifacts": ["/mnt/user-data/outputs/old.pptx"], "messages": [AIMessage(content="Delivered /mnt/user-data/outputs/fake.pptx")]}, run_id=uuid4())
    assert journal.get_delivery_content() == {"presented": 0, "paths": [], "by_tool": {}}


def test_navigation_only_command_and_failed_tool_release_attribution():
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    tool_run = uuid4()
    journal.on_tool_start({"name": "present_files"}, "{}", run_id=tool_run)
    journal.on_tool_error(RuntimeError("failed"), run_id=tool_run)
    assert journal._active_tool_names == {}
    journal.on_tool_end(Command(goto="next"), run_id=uuid4())
    assert journal.get_delivery_content()["presented"] == 0


def test_delivery_fact_copies_do_not_mutate_journal_and_ignore_invalid_paths():
    journal = RunJournal("run", "thread", MemoryRunEventStore())
    journal._record_produced_artifacts(["", None, 1, "/mnt/user-data/outputs/report.md"], "present_files")
    fact = journal.get_delivery_content()
    fact["paths"].clear()
    fact["by_tool"]["present_files"].clear()
    assert journal.get_delivery_content()["paths"] == ["/mnt/user-data/outputs/report.md"]
