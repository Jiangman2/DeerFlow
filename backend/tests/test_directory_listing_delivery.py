from types import SimpleNamespace
from urllib.parse import quote

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.artifact_delivery_middleware import ArtifactDeliveryMiddleware
from deerflow.tools.builtins.present_file_tool import present_file_tool


@pytest.fixture
def listing(tmp_path):
    directory = "/mnt/user-data/outputs/交付/"
    folder = tmp_path / "交付"
    folder.mkdir()
    names = ["报告 (终稿).md", "表一.csv", "汇总.json", "说明.txt", "test_5.md"]
    files = [folder / name for name in names]
    for file in files:
        file.write_text("test")
    state = {"thread_data": {"outputs_path": str(tmp_path)}}
    runtime = SimpleNamespace(context={"thread_id": "th", "run_id": "r1"}, state=state)
    publication = present_file_tool.func(runtime, [str(f) for f in files], "p1").update
    state.update(publication)
    return directory, names, state, runtime


@pytest.mark.parametrize("blocks", [False, True])
def test_directory_and_five_filenames_become_durable_clickable_links(listing, blocks):
    directory, names, state, runtime = listing
    text = f"目录：`{directory}`\n- `{names[0]}`\n| {names[1]} | **{names[2]}** |\n{names[3]}、{names[4]}"
    msg = AIMessage(id="answer", content=[{"type": "text", "text": text}] if blocks else text)
    state["messages"].append(msg)
    mw = ArtifactDeliveryMiddleware()
    repaired = mw.after_model(state, runtime)["messages"][0]
    assert repaired.id == msg.id
    assert repaired.additional_kwargs["deerflow_linked_publication"] is True
    links = repaired.content[-1]["text"] if blocks else repaired.content.removeprefix(text)
    assert links.count("](") == 5
    for name in names:
        assert f"]({quote(directory + name)})" in links
    state["messages"][-1] = repaired
    assert mw.after_model(state, runtime) is None


@pytest.mark.parametrize(
    "reason", ["older_run", "no_stamp", "failed_receipt", "wrong_tool", "missing_file", "not_in_state", "unknown_name", "unknown_list_item", "other_directory", "directory_link", "wrong_relative_path", "two_directories"]
)
def test_directory_listing_fails_closed(listing, tmp_path, reason):
    directory, names, state, runtime = listing
    name = names[1]
    text = f"目录：`{directory}`\n- {name}"
    receipt = state["messages"][0]
    assert isinstance(receipt, ToolMessage)
    if reason == "older_run":
        runtime.context["run_id"] = "r2"
    elif reason == "no_stamp":
        receipt.artifact.pop("publication_run_id")
    elif reason == "failed_receipt":
        receipt.status = "error"
    elif reason == "wrong_tool":
        receipt.name = "bash"
    elif reason == "missing_file":
        (tmp_path / "交付" / name).unlink()
    elif reason == "not_in_state":
        state["artifacts"] = []
    elif reason == "unknown_name":
        text += "\n- 从未发布.md"
    elif reason == "unknown_list_item":
        text += "\n- README"
    elif reason == "other_directory":
        text = text.replace("交付/", "别处/")
    elif reason == "directory_link":
        text += f"\n[下载目录]({directory})"
    elif reason == "wrong_relative_path":
        text += f"\n- ../其他/{name}"
    elif reason == "two_directories":
        text += "\n`/mnt/user-data/outputs/其他/`"
    state["messages"].append(AIMessage(id="answer", content=text))
    assert ArtifactDeliveryMiddleware().after_model(state, runtime)["jump_to"] == "model"
