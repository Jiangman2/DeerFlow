import type { Skill } from "@/core/skills/type";

import type { SkillHubRuntimeSkill } from "./runtime";

export type UserSkillLibraryItem = {
  name: string;
  description: string;
  enabled: boolean;
  editable: boolean;
  installation: SkillHubRuntimeSkill | null;
};

export function buildUserSkillLibrary(
  skills: Skill[],
  installations: SkillHubRuntimeSkill[],
): UserSkillLibraryItem[] {
  const installationBySkillName = new Map(
    installations
      .filter((installation) => installation.installed)
      .map((installation) => [installation.skill_name, installation]),
  );
  const items = skills
    .filter((skill) => skill.category === "custom")
    .map((skill) => {
      const installation = installationBySkillName.get(skill.name) ?? null;
      installationBySkillName.delete(skill.name);
      return {
        name: skill.name,
        description: skill.description,
        // /api/skills is the live runtime inventory and is shared with the
        // Settings view. The registry contributes provenance/version only;
        // its enabled value can be an older snapshot after a Settings toggle.
        enabled: skill.enabled,
        editable: skill.editable,
        installation,
      };
    });

  for (const installation of installationBySkillName.values()) {
    items.push({
      name: installation.skill_name,
      description: "从技能中心添加到当前账号的技能。",
      enabled: installation.enabled,
      editable: false,
      installation,
    });
  }

  return items.sort((left, right) => left.name.localeCompare(right.name));
}
