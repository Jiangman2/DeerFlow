import { expect, test } from "@rstest/core";

import { applySquareSettings, squareKey } from "@/core/agents/square-settings";
import type { Agent, SquareSettings } from "@/core/agents/types";

const seeds: Agent[] = [
  {
    name: "外链",
    description: "原简介",
    model: null,
    tool_groups: null,
    skills: null,
    source: "team",
    external_url: "https://example.com",
  },
  {
    name: "用户发布",
    description: "原简介",
    model: null,
    tool_groups: null,
    skills: null,
    source: "team",
  },
  {
    name: "预制",
    description: "原简介",
    model: null,
    tool_groups: null,
    skills: null,
    source: "legacy",
  },
];
const setting = (
  key: string,
  changes: Partial<SquareSettings>,
): SquareSettings => ({
  key,
  metadata: {},
  hidden: false,
  sort_order: null,
  version: 1,
  ...changes,
});

test("public overrides preserve stable identity and never mutate private/seed data", () => {
  const result = applySquareSettings(seeds, [
    setting("external:外链", {
      metadata: {
        display_name: "新名称",
        external_url: "https://new.example.com",
      },
    }),
  ]);
  expect(result[0]?.name).toBe("外链");
  expect(result[0]?.display_name).toBe("新名称");
  expect(squareKey(result[0]!)).toBe("external:外链");
  expect(seeds[0]?.external_url).toBe("https://example.com");
});

test("withdrawal applies to external, team, and legacy employees; admins can include hidden", () => {
  const settings = seeds.map((agent) =>
    setting(squareKey(agent), { hidden: true }),
  );
  expect(applySquareSettings(seeds, settings)).toHaveLength(0);
  expect(applySquareSettings(seeds, settings, true)).toHaveLength(3);
});

test("global priority is applied before pagination, with stable tie ordering", () => {
  const result = applySquareSettings(seeds, [
    setting("legacy:预制", { sort_order: 0 }),
    setting("team:用户发布", { sort_order: 0 }),
  ]);
  expect(result.map((agent) => agent.name)).toEqual([
    "用户发布",
    "预制",
    "外链",
  ]);
});

test("null values reset overrides and new employees retain default order", () => {
  const result = applySquareSettings(seeds, [
    setting("external:外链", {
      metadata: { display_name: null },
      sort_order: null,
    }),
  ]);
  expect(result.map((agent) => agent.name)).toEqual(
    seeds.map((agent) => agent.name),
  );
  expect(result[0]?.display_name).toBeUndefined();
});

test("server-filtered withdrawals do not change default priorities between admin and regular viewers", () => {
  const catalog = seeds.map((agent, index) =>
    index === 0 ? agent : { ...agent, square_base_order: index - 1 },
  );
  const settings = [
    setting("team:用户发布", { hidden: true }),
    setting("external:外链", { sort_order: 25 }),
  ];
  const adminVisible = applySquareSettings(catalog, settings).map(
    (agent) => agent.name,
  );
  const regularVisible = applySquareSettings(
    catalog.filter((agent) => agent.name !== "用户发布"),
    settings,
  ).map((agent) => agent.name);
  expect(regularVisible).toEqual(adminVisible);
});
