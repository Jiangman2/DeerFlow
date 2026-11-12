import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.utils.file_conversion import _convert_with_markitdown, _do_convert


@pytest.mark.parametrize("explicit_options", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_worker_bounds_native_pool_and_restores_constructor(monkeypatch, tmp_path, explicit_options, failure):
    options = SimpleNamespace(add_session_config_entry=MagicMock())
    constructor = MagicMock(return_value=object())
    ort = SimpleNamespace(InferenceSession=constructor, SessionOptions=lambda: options, ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL="sequential"))
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    class Converter:
        def __init__(self):
            ort.InferenceSession("original-model.onnx", options if explicit_options else None, providers=["CPUExecutionProvider"])
            if failure:
                raise RuntimeError("synthetic-init-failure")

        def convert(self, path):
            assert path == str(tmp_path / "input.docx")
            return SimpleNamespace(text_content="complete text")

    monkeypatch.setitem(sys.modules, "markitdown", SimpleNamespace(MarkItDown=Converter))
    if failure:
        with pytest.raises(RuntimeError, match="synthetic-init-failure"):
            _do_convert(tmp_path / "input.docx", "auto", bounded_threads=True)
    else:
        assert _do_convert(tmp_path / "input.docx", "auto", bounded_threads=True) == "complete text"
    assert options.intra_op_num_threads == options.inter_op_num_threads == 1
    assert options.execution_mode == "sequential"
    assert options.add_session_config_entry.call_count == 2
    constructor.assert_called_once_with("original-model.onnx", options, providers=["CPUExecutionProvider"])
    assert ort.InferenceSession is constructor


def test_ordinary_converter_does_not_modify_process_global_onnx(monkeypatch, tmp_path):
    constructor = MagicMock()
    ort = SimpleNamespace(InferenceSession=constructor)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)

    class Converter:
        def __init__(self):
            assert ort.InferenceSession is constructor

        def convert(self, path):
            return SimpleNamespace(text_content="unchanged")

    monkeypatch.setitem(sys.modules, "markitdown", SimpleNamespace(MarkItDown=Converter))
    assert _convert_with_markitdown(tmp_path / "input.docx") == "unchanged"
    assert ort.InferenceSession is constructor
