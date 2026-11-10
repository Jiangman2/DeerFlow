from types import SimpleNamespace
from urllib.parse import quote

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.artifact_delivery_middleware import ArtifactDeliveryMiddleware, has_invalid_artifact_claim

PREFIX = "/mnt/user-data/outputs/"


def fixture(tmp_path, text, names, *, run_id="run-1"):
    paths = [PREFIX + name for name in names]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"published fixture")
    state = {
        "thread_data": {"outputs_path": str(tmp_path)},
        "artifacts": paths,
        "messages": [
            ToolMessage(content="published", tool_call_id="present", name="present_files", artifact={"publication_run_id": run_id, "presented_files": paths}),
            AIMessage(id="final", content=text),
        ],
    }
    return state, SimpleNamespace(context={"thread_id": "thread-1", "run_id": "run-1"})


@pytest.mark.parametrize("directories", [["模板包/"], ["模板包/", ""]])
@pytest.mark.parametrize("blocks", [False, True])
def test_directory_narrative_gets_current_run_links(tmp_path, directories, blocks):
    text = "文件位置：" + "、".join(f"`{PREFIX}{d}`" for d in directories)
    text += "\n\n## 使用方式\n1. 先确认需求，再逐项填写。\n- 检查字段完整性\n`项目编号`由业务系统提供。"
    names = [d + name for d in directories for name in ["附件1-需求（模板）.docx", "附件1-需求（模板）.preview.pdf"]]
    state, runtime = fixture(tmp_path, [{"type": "text", "text": text}] if blocks else text, names)
    repaired = ArtifactDeliveryMiddleware().after_model(state, runtime)["messages"][0]
    assert repaired.id == "final"
    assert repaired.additional_kwargs["deerflow_linked_publication"]
    content = repaired.content[-1]["text"] if blocks else repaired.content
    for name in names:
        assert f"]({quote(PREFIX + name, safe='/')})" in content
    assert not has_invalid_artifact_claim(repaired, state)


def test_mixed_presentation_images_receive_specific_recovery_and_partial_links(tmp_path):
    text = f"[报告]({PREFIX}报告.pptx)\n" + "\n".join(f"![预览]({PREFIX}s{i}.png)" for i in (1, 3, 6, 10))
    state, runtime = fixture(tmp_path, text, ["报告.pptx", "报告.preview.pdf"])
    # Even a real local image is not automatically published.
    (tmp_path / "s1.png").write_bytes(b"png")
    mw = ArtifactDeliveryMiddleware()
    assert mw.after_model(state, runtime)["jump_to"] == "model"
    request = SimpleNamespace(runtime=runtime, messages=[], override=lambda **kw: kw)
    prompt = mw._augment_request(request)["messages"][-1].content
    assert '"unverified": [' in prompt and "s10.png" in prompt
    assert '"verified": [' in prompt and "报告.pptx" in prompt
    assert "不必重新生成" in prompt
    failed = mw.after_model(state, runtime)["messages"][0]
    assert failed.additional_kwargs["deerflow_error_fallback"]
    assert "未完整通过校验" in failed.content
    assert f"]({quote(PREFIX + '报告.pptx', safe='/')})" in failed.content
    assert "s1.png" not in failed.content
    assert not has_invalid_artifact_claim(failed, state)


@pytest.mark.parametrize("problem", ["old_run", "missing", "unpublished", "wrong_directory", "directory_link", "unknown_file", "traversal", "too_many"])
def test_narrative_cannot_invent_publication(tmp_path, problem):
    text = f"已生成：`{PREFIX}包/`。\n请检查内容。"
    names = ["包/报告.docx", "包/报告.preview.pdf"]
    if problem == "too_many":
        names = [f"包/{i}.txt" for i in range(41)]
    state, runtime = fixture(tmp_path, text, names, run_id="old-run" if problem == "old_run" else "run-1")
    if problem == "missing":
        (tmp_path / names[0]).unlink()
    elif problem == "unpublished":
        state["artifacts"] = []
    elif problem == "wrong_directory":
        state["messages"][-1].content += f"另一个目录：`{PREFIX}不存在/`"
    elif problem == "directory_link":
        state["messages"][-1].content = f"[下载]({PREFIX}包/)"
    elif problem == "unknown_file":
        state["messages"][-1].content += "\n- 未生成的报告.xlsx"
    elif problem == "traversal":
        state["messages"][-1].content = f"`{PREFIX}包/../`"
    assert ArtifactDeliveryMiddleware().after_model(state, runtime)["jump_to"] == "model"


def test_partial_fallback_never_salvages_prior_run_or_missing_files(tmp_path):
    state, runtime = fixture(tmp_path, f"[遗漏]({PREFIX}missing.png)", ["old.pptx"], run_id="old")
    failed = ArtifactDeliveryMiddleware(max_retries=0).after_model(state, runtime)["messages"][0]
    assert "old.pptx" not in failed.content
    assert failed.additional_kwargs["deerflow_error_fallback"]


def test_terminal_loop_stop_is_not_repaired(tmp_path):
    state, runtime = fixture(tmp_path, f"`{PREFIX}包/`", ["包/报告.docx"])
    state["messages"][-1].additional_kwargs["deerflow_terminal_stop"] = True
    assert ArtifactDeliveryMiddleware().after_model(state, runtime) is None


def test_publication_symlink_outside_owner_directory_is_rejected(tmp_path):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    state, runtime = fixture(outputs, f"`{PREFIX}包/`", ["包/报告.txt"])
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    target = outputs / "包/报告.txt"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable on this host")
    assert ArtifactDeliveryMiddleware().after_model(state, runtime)["jump_to"] == "model"
