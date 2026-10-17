import { expect, test } from "@rstest/core";

import { filterDigitalEmployees } from "@/core/agents/digital-employee-filter";
import { applyEmployeeMetadata } from "@/core/agents/employee-directory";
import type { Agent } from "@/core/agents/types";

const employee: Agent = {
  name: "one",
  source: "team",
  description: "",
  model: null,
  skills: [],
  tool_groups: [],
  tags: ["热门"],
};

test("ordinary tags and missing metadata never imply hot", () => {
  const agents = applyEmployeeMetadata([employee], []);
  expect(filterDigitalEmployees(agents, { hotOnly: true })).toEqual([]);
  expect(filterDigitalEmployees(agents, {})).toHaveLength(1);
});

test("metadata is scoped by source and includes trusted creator", () => {
  const agents = applyEmployeeMetadata(
    [employee, { ...employee, source: "personal" }],
    [
      {
        name: "one",
        source: "team",
        is_hot: true,
        version: 2,
        identity_token: "a".repeat(64),
        creator_name: "张研究员",
      },
    ],
  );
  expect(agents[0]?.creator_name).toBe("张研究员");
  expect(filterDigitalEmployees(agents, { hotOnly: true })).toHaveLength(1);
  expect(agents[1]?.is_hot).toBe(false);
});

test("external cards cannot borrow a same-named team flag", () => {
  const agents = applyEmployeeMetadata(
    [{ ...employee, external_url: "https://example.test" }],
    [
      {
        name: "one",
        source: "team",
        is_hot: true,
        version: 1,
        identity_token: "b".repeat(64),
        creator_name: null,
      },
    ],
  );
  expect(agents[0]?.is_hot).toBe(false);
});
