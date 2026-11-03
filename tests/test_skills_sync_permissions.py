from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_SYNC = REPO_ROOT / "k8s" / "local-pvc" / "skills-sync.yaml"


def test_skills_sync_normalizes_only_platform_owned_skill_trees() -> None:
    manifest = SKILLS_SYNC.read_text(encoding="utf-8")

    assert "for shared_tree in public third_party" in manifest
    assert 'chmod -R a+rX "/mnt/skills/${shared_tree}"' in manifest
    assert "for shared_tree in public third_party custom" not in manifest


def test_skills_sync_keeps_the_shared_pvc_read_only_for_sandboxes() -> None:
    stack = (REPO_ROOT / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml").read_text(encoding="utf-8")

    assert "name: SKILLS_PVC_NAME" in stack
    assert "value: deer-flow-skills-pvc" in stack
    assert "claimName: deer-flow-skills-pvc" in SKILLS_SYNC.read_text(encoding="utf-8")
