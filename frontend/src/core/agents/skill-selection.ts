import type { Skill } from "@/core/skills/type";

export const MAX_AGENT_SKILLS = 128;

// Selection is not a publish grant. Keep missing saved names visible so a
// catalog refresh cannot silently drop dependencies from an existing employee.
export function agentSkillChoices(skills: Skill[], selected: string[] | null) {
  const available = new Map(
    skills.filter((skill) => skill.enabled).map((skill) => [skill.name, skill]),
  );
  const names = new Set([...available.keys(), ...(selected ?? [])]);
  return [...names].sort().map((name) => ({
    name,
    description: available.get(name)?.description ?? "",
    available: available.has(name),
    selected: selected?.includes(name) ?? false,
  }));
}

export function toggleAgentSkill(selected: string[], name: string): string[] {
  if (selected.includes(name)) return selected.filter((item) => item !== name);
  if (selected.length >= MAX_AGENT_SKILLS) return selected;
  return [...selected, name];
}
