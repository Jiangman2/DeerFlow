import { expect, test } from "@rstest/core";

import {
  collectDigitalEmployeeTags,
  filterDigitalEmployees,
} from "@/core/agents/digital-employee-filter";
import type { Agent } from "@/core/agents/types";

const agents: Agent[] = [
  {
    name: "report-assistant",
    display_name: "科研报告数字员工",
    description: "自动整理勘探研究成果",
    model: null,
    tool_groups: [],
    skills: ["documents"],
    category_id: "research",
    tags: ["报告", "勘探"],
  },
  {
    name: "hse-reviewer",
    display_name: "HSE 审查员",
    description: "检查现场安全风险",
    model: null,
    tool_groups: ["risk"],
    skills: [],
    category_id: "safety",
    tags: ["安全", "审查"],
  },
];

test("fuzzy search covers name, display name, description, tags, and capabilities", () => {
  expect(filterDigitalEmployees(agents, { query: "科研 勘探" })).toEqual([
    agents[0],
  ]);
  expect(filterDigitalEmployees(agents, { query: "risk" })).toEqual([
    agents[1],
  ]);
  expect(
    filterDigitalEmployees(agents, {
      query: "研究与报告",
      categoryNames: { research: "研究与报告", safety: "安全环保" },
    }),
  ).toEqual([agents[0]]);
});

test("category and tag filters intersect and tags are counted", () => {
  expect(
    filterDigitalEmployees(agents, {
      categoryId: "research",
      tags: ["报告"],
    }),
  ).toEqual([agents[0]]);
  expect(collectDigitalEmployeeTags(agents)).toEqual([
    { label: "安全", count: 1 },
    { label: "报告", count: 1 },
    { label: "勘探", count: 1 },
    { label: "审查", count: 1 },
  ]);
});
