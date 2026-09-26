import { describe, expect, it } from "@rstest/core";

import { agentSkillChoices, MAX_AGENT_SKILLS, toggleAgentSkill } from "@/core/agents/skill-selection";
import type { Skill } from "@/core/skills/type";

const skills: Skill[] = [
  { name: "report", description: "Reports", enabled: true, editable: true, category: "custom", license: "" },
  { name: "disabled", description: "Disabled", enabled: false, editable: true, category: "custom", license: "" },
];

describe("employee skill selection", () => {
  it("lists only enabled skills without treating inheritance as explicit selection", () => {
    expect(agentSkillChoices(skills, null)).toEqual([{ name: "report", description: "Reports", available: true, selected: false }]);
  });
  it("keeps unavailable saved names visible instead of silently dropping them", () => {
    const choices = agentSkillChoices(skills, ["disabled", "missing"]);
    expect(choices.filter((item) => item.selected).map((item) => item.name)).toEqual(["disabled", "missing"]);
    expect(choices.filter((item) => item.selected).every((item) => !item.available)).toBe(true);
  });
  it("does not broaden an explicitly empty selection", () => {
    expect(agentSkillChoices(skills, []).some((item) => item.selected)).toBe(false);
  });
  it("removes unavailable names without mutating the saved array", () => {
    const original = ["missing"];
    expect(toggleAgentSkill(original, "missing")).toEqual([]);
    expect(original).toEqual(["missing"]);
  });
  it("deduplicates and bounds additions while still permitting removals", () => {
    expect(toggleAgentSkill(["report"], "report")).toEqual([]);
    const full = Array.from({ length: MAX_AGENT_SKILLS }, (_, index) => `skill-${index}`);
    expect(toggleAgentSkill(full, "another")).toBe(full);
    expect(toggleAgentSkill(full, "skill-0")).toHaveLength(MAX_AGENT_SKILLS - 1);
  });
});
