"""Regression tests for runtime skill discovery boundaries."""

from pathlib import Path
from unittest.mock import patch

from deerflow.config.paths import Paths
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage


def _write_skill(root: Path, relative_dir: str) -> Path:
    skill_file = root / relative_dir / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("---\nname: demo\ndescription: demo\n---\n", encoding="utf-8")
    return skill_file


def test_local_storage_excludes_eval_fixture_skills(tmp_path: Path):
    expected = _write_skill(tmp_path, "public/real-skill")
    fixture = _write_skill(tmp_path, "public/skill-reviewer/evals/fixtures/publish-candidate")

    discovered = [skill_file for _, _, skill_file in LocalSkillStorage(host_path=str(tmp_path))._iter_skill_files()]

    assert expected in discovered
    assert fixture not in discovered


def test_user_scoped_storage_excludes_eval_fixture_skills(tmp_path: Path):
    skills_root = tmp_path / "skills"
    expected = _write_skill(skills_root, "public/real-skill")
    fixture = _write_skill(skills_root, "public/skill-reviewer/evals/fixtures/publish-candidate")

    with patch("deerflow.config.paths.get_paths", return_value=Paths(base_dir=tmp_path)):
        storage = UserScopedSkillStorage("test-user", host_path=str(skills_root))
    discovered = [skill_file for _, _, skill_file in storage._iter_skill_files()]

    assert expected in discovered
    assert fixture not in discovered
