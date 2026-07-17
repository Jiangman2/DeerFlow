from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_frontend_design_does_not_require_deerflow_branding() -> None:
    skill = (REPO_ROOT / "skills" / "public" / "frontend-design" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    normalized = skill.casefold()

    assert "created by deerflow" not in normalized
    assert "branding requirement" not in normalized
    assert "deerflow.tech" not in normalized
