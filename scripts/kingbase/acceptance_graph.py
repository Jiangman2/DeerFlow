"""Real interrupted graph used by the database acceptance probe."""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ProbeState(TypedDict):
    request: str
    result: str


def confirm(state: ProbeState) -> dict:
    answer = interrupt({"request": state["request"]})
    return {"result": answer}


def build_graph(saver):
    graph = StateGraph(ProbeState)
    graph.add_node("confirm", confirm)
    graph.add_edge(START, "confirm")
    graph.add_edge("confirm", END)
    return graph.compile(checkpointer=saver)
