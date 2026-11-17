import { describe, expect, it } from "@rstest/core";

import {
  buildStandardsAgentHref,
  MAX_STANDARDS_PORTAL_CONFIG_LENGTH,
  parseStandardsPortalConfig,
  STANDARDS_MODULE_KEYS,
} from "@/core/standards-portal/config";

describe("standards portal runtime configuration", () => {
  it("supports every portal module with Unicode native employee names", () => {
    const names = {
      qa: "标准知识问答助手",
      search: "标准信息检索",
      policy: "政策研究助手",
      comparison: "标准对比-2026",
      applicability: "适用性分析",
      compliance: "合规自检",
      authoring: "标准编审辅助",
    };
    expect(parseStandardsPortalConfig(JSON.stringify(names))).toEqual({
      modules: names,
      issues: [],
    });
    expect(Object.keys(names)).toEqual([...STANDARDS_MODULE_KEYS]);
  });

  it("normalizes Latin letters like native employee creation", () => {
    expect(parseStandardsPortalConfig('{"qa":"Standard-QA"}')).toEqual({
      modules: { qa: "standard-qa" },
      issues: [],
    });
  });

  it("keeps an unset, blank, or empty object unconfigured", () => {
    for (const raw of [undefined, "", "  \n\t", "{}", '{"qa":""}']) {
      expect(parseStandardsPortalConfig(raw)).toEqual({
        modules: {},
        issues: [],
      });
    }
  });

  it("isolates invalid module entries while preserving valid mappings", () => {
    expect(
      parseStandardsPortalConfig(
        JSON.stringify({
          qa: "标准问答",
          search: ["检索助手"],
          policy: null,
          comparison: 12,
          applicability: { name: "适用性分析" },
          compliance: false,
          authoring: "编审助手",
        }),
      ),
    ).toEqual({
      modules: { qa: "标准问答", authoring: "编审助手" },
      issues: [
        { code: "invalid_agent_name", module: "search" },
        { code: "invalid_agent_name", module: "policy" },
        { code: "invalid_agent_name", module: "comparison" },
        { code: "invalid_agent_name", module: "applicability" },
        { code: "invalid_agent_name", module: "compliance" },
      ],
    });
  });

  it("ignores unknown and prototype keys without echoing their contents", () => {
    expect(
      parseStandardsPortalConfig(
        '{"qa":"问答助手","__proto__":{"polluted":true},"constructor":"other","sensitive-key":"do-not-log"}',
      ),
    ).toEqual({
      modules: { qa: "问答助手" },
      issues: [{ code: "unknown_module" }],
    });
    expect(Object.prototype).not.toHaveProperty("polluted");
  });

  it("does not expose malformed JSON in diagnostics", () => {
    expect(parseStandardsPortalConfig('{"qa":"private-input"')).toEqual({
      modules: {},
      issues: [{ code: "invalid_json" }],
    });
  });

  it("rejects non-object roots", () => {
    for (const raw of ["null", "[]", "true", "123", '"employee"']) {
      expect(parseStandardsPortalConfig(raw)).toEqual({
        modules: {},
        issues: [{ code: "invalid_root" }],
      });
    }
  });

  it("bounds raw input before parsing, including whitespace-only input", () => {
    for (const raw of [
      " ".repeat(MAX_STANDARDS_PORTAL_CONFIG_LENGTH + 1),
      JSON.stringify({ qa: "x".repeat(MAX_STANDARDS_PORTAL_CONFIG_LENGTH) }),
    ]) {
      expect(parseStandardsPortalConfig(raw)).toEqual({
        modules: {},
        issues: [{ code: "too_large" }],
      });
    }
  });

  it("counts Unicode code points consistently with the backend name limit", () => {
    const name = "\u{20000}".repeat(64);
    expect(
      parseStandardsPortalConfig(JSON.stringify({ qa: name })).modules,
    ).toEqual({
      qa: name,
    });
    expect(
      parseStandardsPortalConfig(JSON.stringify({ qa: `${name}字` })).issues,
    ).toEqual([{ code: "invalid_agent_name", module: "qa" }]);
    expect(buildStandardsAgentHref("a".repeat(64))).toBeDefined();
    expect(buildStandardsAgentHref("a".repeat(65))).toBeUndefined();
  });
});

describe("standards employee new-chat links", () => {
  it("encodes a raw Chinese employee name once into a same-origin route", () => {
    const href = buildStandardsAgentHref("标准问答");
    expect(href).toBe(
      "/workspace/agents/%E6%A0%87%E5%87%86%E9%97%AE%E7%AD%94/chats/new",
    );
    expect(href).not.toContain("%25");
    expect(new URL(href!, "https://portal.example").origin).toBe(
      "https://portal.example",
    );
  });

  it("rejects addresses, encoded input, traversal, and invalid native names", () => {
    const invalidNames = [
      "",
      " ",
      ".",
      "..",
      "foo/bar",
      "foo\\bar",
      "//external.example/employee",
      "https://external.example/employee",
      "/workspace/agents/employee/chats/new",
      "%E6%A0%87%E5%87%86",
      "employee?next=outside",
      "employee#fragment",
      "employee_name",
      "employee\n",
      "em\u0000ployee",
      "em\u202eployee",
      "employee😀",
      "employee\ud800",
      "İ", // Lowercasing introduces a combining mark, invalid in native routes.
    ];
    for (const name of invalidNames) {
      expect(buildStandardsAgentHref(name)).toBeUndefined();
      expect(
        parseStandardsPortalConfig(JSON.stringify({ qa: name })).modules,
      ).toEqual({});
      if (name !== "") {
        expect(
          parseStandardsPortalConfig(JSON.stringify({ qa: name })).issues,
        ).toEqual([{ code: "invalid_agent_name", module: "qa" }]);
      }
    }
  });
});
