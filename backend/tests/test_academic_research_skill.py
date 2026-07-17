from pathlib import Path

from deerflow.skills.storage.local_skill_storage import LocalSkillStorage

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"
ADAPTER_ROOT = SKILLS_ROOT / "public" / "academic-research"
VENDORED_ROOT = SKILLS_ROOT / "third_party" / "academic-research-skills"
INTERNAL_COMPONENTS = {
    "academic-paper",
    "academic-paper-reviewer",
    "academic-pipeline",
}


def test_academic_research_uses_one_public_adapter_and_an_unscanned_vendor_root() -> None:
    assert (ADAPTER_ROOT / "SKILL.md").is_file()
    assert (VENDORED_ROOT / "LICENSE").is_file()
    assert not (ADAPTER_ROOT / "third_party").exists()

    adapter = (ADAPTER_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "/mnt/skills/third_party/academic-research-skills" in adapter


def test_vendored_academic_components_are_runtime_resources_not_platform_skills() -> None:
    storage = LocalSkillStorage(host_path=str(SKILLS_ROOT))
    skills = storage.load_skills(enabled_only=False)
    by_name = {skill.name: skill for skill in skills}

    assert "academic-research" in by_name
    assert INTERNAL_COMPONENTS.isdisjoint(by_name)
    assert by_name["deep-research"].skill_path == "deep-research"

    for component in (*sorted(INTERNAL_COMPONENTS), "deep-research"):
        assert (VENDORED_ROOT / component / "SKILL.md").is_file()
