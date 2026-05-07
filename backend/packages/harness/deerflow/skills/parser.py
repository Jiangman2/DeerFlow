import re
from pathlib import Path

import yaml

from .types import Skill


def parse_skill_file(skill_file: Path, category: str, relative_path: Path | None = None) -> Skill | None:
    """
    Parse a SKILL.md file and extract metadata.

    Args:
        skill_file: Path to the SKILL.md file
        category: Category of the skill ('public' or 'custom')

    Returns:
        Skill object if parsing succeeds, None otherwise
    """
    if not skill_file.exists() or skill_file.name != "SKILL.md":
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")
        # Be tolerant of UTF-8 BOM from editors on Windows.
        if content.startswith("\ufeff"):
            content = content.lstrip("\ufeff")

        # Extract YAML front matter
        # Pattern: --- ... --- (accept both \n and \r\n line endings)
        front_matter_match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n", content, re.DOTALL)

        if not front_matter_match:
            return None

        front_matter = front_matter_match.group(1)

        # Parse YAML front matter with full YAML support (multiline strings, folded style, etc.)
        metadata = yaml.safe_load(front_matter)
        if not isinstance(metadata, dict):
            return None

        # Extract required fields
        name = metadata.get("name")
        description = metadata.get("description")

        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(description, str) or not description.strip():
            return None

        license_text = metadata.get("license")
        if license_text is not None and not isinstance(license_text, str):
            license_text = str(license_text)

        return Skill(
            name=name.strip(),
            description=description.strip(),
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            enabled=True,  # Default to enabled, actual state comes from config file
        )

    except Exception as e:
        print(f"Error parsing skill file {skill_file}: {e}")
        return None
