import { describe, expect, it } from "@rstest/core";

import {
  digitalEmployeeTagLength,
  MAX_DIGITAL_EMPLOYEE_TAGS,
  normalizeDigitalEmployeeTags,
  suggestDigitalEmployeeTags,
} from "@/core/agents/digital-employee-tags";

describe("digital employee tags", () => {
  it("counts Unicode characters consistently with the backend", () => {
    expect(digitalEmployeeTagLength("海油😀")).toBe(3);
  });
  it("trims, deduplicates case-insensitively, and caps tags", () => {
    const tags = normalizeDigitalEmployeeTags([
      " 数据分析 ",
      "数据分析",
      "AI",
      "ai",
      "一",
      "二",
      "三",
      "四",
      "五",
      "六",
      "七",
      "八",
    ]);
    expect(tags).toEqual([
      "数据分析",
      "AI",
      "一",
      "二",
      "三",
      "四",
      "五",
      "六",
    ]);
    expect(tags).toHaveLength(MAX_DIGITAL_EMPLOYEE_TAGS);
  });

  it("suggests tags from category and employee profile", () => {
    expect(
      suggestDigitalEmployeeTags({
        name: "月报分析",
        description: "汇总生产数据并生成 PPT 报告",
        categoryId: "digital",
      }),
    ).toEqual(expect.arrayContaining(["数智技术", "数据分析", "文档生成"]));
  });

  it("provides a useful fallback", () => {
    expect(suggestDigitalEmployeeTags({ name: "协作助手" })).toEqual([
      "通用协作",
    ]);
  });
});
