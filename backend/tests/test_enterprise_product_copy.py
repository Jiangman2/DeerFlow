from __future__ import annotations

import os
from pathlib import Path


def test_deprecated_enterprise_product_names_are_absent_from_runtime_source() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = ("中海油勘探开发" + "智能体", "中海油勘探开发" + "通用智能体")
    checked_roots = (root / "backend", root / "frontend" / "src")
    violations: list[str] = []

    skipped = {".git", ".next", ".venv", "node_modules", "__pycache__"}
    extensions = {".py", ".ts", ".tsx", ".json", ".yaml", ".yml"}
    for checked_root in checked_roots:
        for current, directories, filenames in os.walk(checked_root):
            directories[:] = [name for name in directories if name not in skipped]
            for filename in filenames:
                path = Path(current) / filename
                if path.suffix.lower() not in extensions:
                    continue
                body = path.read_text(encoding="utf-8", errors="ignore")
                if any(name in body for name in forbidden):
                    violations.append(str(path.relative_to(root)))

    assert not violations, "deprecated enterprise product copy remains in: " + ", ".join(violations)
