import io
import json
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from pydantic import ValidationError

from deerflow.tools.builtins.local_data_tool import LocalDataJob, _xlsx, process_job


@pytest.fixture()
def root(tmp_path):
    for name in ("workspace", "uploads", "outputs"):
        (tmp_path / name).mkdir()
    return tmp_path


def output_path(root, virtual):
    return root / virtual.removeprefix("/mnt/user-data/")


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow FD boundary")
def test_real_csv_aggregation_docx_excel_chart_and_formula_literal(root):
    (root / "uploads/source.csv").write_text("项目,金额\n甲,12\n乙,7\n甲,3\n", encoding="utf-8")
    result = process_job(
        root,
        LocalDataJob(
            source_path="/mnt/user-data/uploads/source.csv",
            group_by="项目",
            value_column="金额",
            aggregation="sum",
            formats=["docx", "xlsx", "svg", "csv"],
            title="收支分析",
            paragraphs=["以下为授权本地数据汇总。 <script> is literal text."],
        ),
    )
    files = {Path(name).suffix: output_path(root, name) for name in result["files"]}
    columns, rows = _xlsx(files[".xlsx"].read_bytes())
    assert columns == ["项目", "sum"] and rows == [["甲", "15.0"], ["乙", "7.0"]]
    with zipfile.ZipFile(files[".docx"]) as package:
        document = package.read("word/document.xml")
        ElementTree.fromstring(document)
        assert b"000000" in document and b"&lt;script&gt;" in document
        assert all("external" not in name for name in package.namelist())
    svg = files[".svg"].read_text()
    assert "<script" not in svg and "href=" not in svg
    ElementTree.fromstring(svg)
    job = LocalDataJob(columns=["text"], rows=[['=WEBSERVICE("https://external")']], formats=["xlsx", "csv"])
    second = process_job(root, job)
    for name in second["files"]:
        data = output_path(root, name).read_bytes()
        if name.endswith("xlsx"):
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                assert b"<f>" not in package.read("xl/worksheets/sheet1.xml")
        else:
            assert "'=WEBSERVICE" in data.decode("utf-8-sig")


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow FD boundary")
@pytest.mark.parametrize("path", ["https://example.com/data.csv", "/etc/passwd", "/mnt/user-data/uploads/../../secret.csv", "/mnt/user-data/uploads/link.csv", "/mnt/user-data/uploads/dir/secret.csv", "/mnt/user-data/uploads/hard.csv"])
def test_traversal_links_and_other_roots_are_rejected(root, tmp_path, path):
    outside = tmp_path.parent / (tmp_path.name + "-private")
    outside.mkdir()
    (outside / "secret.csv").write_text("x\nprivate\n")
    (root / "uploads/link.csv").symlink_to(outside / "secret.csv")
    (root / "uploads/dir").symlink_to(outside, target_is_directory=True)
    os.link(outside / "secret.csv", root / "uploads/hard.csv")
    with pytest.raises((ValueError, OSError)):
        process_job(root, LocalDataJob(source_path=path))
    assert not list((root / "workspace").iterdir())


def test_arbitrary_code_and_external_template_parameters_rejected():
    for fields in ({"python": "import os"}, {"command": "curl"}, {"template": "https://external"}, {"aggregation": "eval"}):
        with pytest.raises(ValidationError):
            LocalDataJob.model_validate(fields)


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow FD boundary")
def test_limits_and_json_shape_fail_before_outputs(root):
    (root / "uploads/bad.json").write_text(json.dumps({"url": "https://example.com"}))
    with pytest.raises(ValueError, match="json_requires"):
        process_job(root, LocalDataJob(source_path="/mnt/user-data/uploads/bad.json"))
    with pytest.raises(ValueError, match="row_width"):
        process_job(root, LocalDataJob(columns=["a"], rows=[[1, 2]]))
    assert not list((root / "workspace").iterdir())
    for value in ({"columns": "a", "rows": [[1]]}, {"columns": ["a"], "rows": [[{"url": "https://external"}]]}):
        (root / "uploads/bad.json").write_text(json.dumps(value))
        with pytest.raises(ValueError):
            process_job(root, LocalDataJob(source_path="/mnt/user-data/uploads/bad.json"))


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow FD boundary")
def test_generated_excel_readable_by_independent_reader(root):
    openpyxl = pytest.importorskip("openpyxl")
    result = process_job(root, LocalDataJob(columns=["项目", "金额"], rows=[["甲", 12], ["=1+1", 3]], formats=["xlsx"]))
    workbook = openpyxl.load_workbook(output_path(root, result["files"][0]), data_only=False)
    try:
        assert list(workbook.active.values) == [("项目", "金额"), ("甲", 12), ("=1+1", 3)]
        assert workbook.active["A3"].data_type == "s"
    finally:
        workbook.close()


@pytest.mark.skipif(os.name != "posix", reason="Linux no-follow FD boundary")
def test_tool_resolves_current_runtime_owner_and_thread(root, monkeypatch):
    from deerflow.tools.builtins import local_data_tool as tool_module

    resolved = []

    def sandbox_root(thread_id, *, user_id):
        resolved.append((thread_id, user_id))
        return root

    monkeypatch.setattr(tool_module, "get_paths", lambda: SimpleNamespace(sandbox_user_data_dir=sandbox_root))
    monkeypatch.setattr(tool_module, "resolve_runtime_user_id", lambda runtime: runtime.context["user_id"])
    runtime = SimpleNamespace(context={"user_id": "owner-a", "thread_id": "thread-a"})
    result = json.loads(tool_module.process_local_data.func(runtime, LocalDataJob(columns=["值"], rows=[[1]], formats=["csv"])))
    assert result["status"] == "local_processing_complete"
    assert resolved == [("thread-a", "owner-a")]
    assert output_path(root, result["files"][0]).is_file()
    runtime.context.pop("thread_id")
    denied = json.loads(tool_module.process_local_data.func(runtime, LocalDataJob(columns=["值"], rows=[[1]])))
    assert denied["error"] == "thread_identity_required" and denied["retryable"] is False
    assert len(resolved) == 1
