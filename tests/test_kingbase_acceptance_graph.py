"""Verify the acceptance scenario itself before using a real server."""
import asyncio
import importlib.util
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

spec = importlib.util.spec_from_file_location("acceptance_graph", Path(__file__).parents[1] / "scripts" / "kingbase" / "acceptance_graph.py")
scenario = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scenario)


class GraphTests(unittest.TestCase):
    def test_interrupt_then_rebuild_and_resume(self):
        async def run():
            saver = InMemorySaver()
            config = {"configurable": {"thread_id": "scenario-test"}}
            first = await scenario.build_graph(saver).ainvoke({"request": "确认恢复"}, config)
            self.assertTrue(first["__interrupt__"])
            second = await scenario.build_graph(saver).ainvoke(Command(resume="已恢复"), config)
            self.assertEqual(second["result"], "已恢复")
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
