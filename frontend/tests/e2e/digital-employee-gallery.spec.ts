import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const EMPLOYEES = [
  {
    name: "report-assistant",
    display_name: "Research Report Employee",
    description: "Organizes exploration findings into clear reports",
    model: "builder-flash",
    tool_groups: ["documents"],
    skills: ["research"],
    category_id: "general",
    tags: ["Reports", "Exploration"],
    avatar_id: "report-research",
    source: "personal" as const,
  },
  {
    name: "hse-reviewer",
    display_name: "HSE Safety Reviewer",
    description: "Checks field safety risks and procedures",
    model: null,
    tool_groups: ["risk"],
    skills: [],
    category_id: "general",
    tags: ["Safety", "Review"],
    avatar_id: "hse",
    source: "personal" as const,
  },
  {
    name: "seismic-helper",
    display_name: "Seismic Interpretation Employee",
    description: "Interprets seismic sections and anomalies",
    model: null,
    tool_groups: ["SEGY"],
    skills: [],
    category_id: "general",
    tags: ["Seismic"],
    avatar_id: "seismic-processing",
    source: "personal" as const,
  },
  ...Array.from({ length: 7 }, (_, index) => ({
    name: `employee-${index + 4}`,
    display_name: `Digital Employee ${index + 4}`,
    description: `Handles workflow ${index + 4}`,
    model: null,
    tool_groups: ["documents"],
    skills: [],
    category_id: "general",
    tags: [`Tag ${index + 4}`],
    avatar_id: "platform-management",
    source: "personal" as const,
  })),
];

test("digital employee directory filters compact rows on desktop and mobile", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, {
    agents: EMPLOYEES,
    teamAgents: EMPLOYEES.map((agent) => ({ ...agent, source: "team" })),
  });
  await page.context().addCookies([
    {
      name: "locale",
      value: "zh-CN",
      url: String(testInfo.project.use.baseURL ?? "http://localhost:3000"),
    },
  ]);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/workspace/agents");

  const cards = page.getByTestId("digital-employee-card");
  const pagination = page.getByTestId("digital-employee-pagination");
  await expect(cards).toHaveCount(6);
  await expect(pagination).toBeVisible();
  await pagination.getByRole("button", { name: /Next|下一页/i }).click();
  await expect(cards).toHaveCount(6);

  await page
    .getByRole("tab", { name: /My digital employees|我的数字员工/i })
    .click();

  await expect(cards).toHaveCount(6);
  await expect(pagination).toBeVisible();
  await expect(
    pagination.getByRole("button", { name: /Page 1|第 1 页/i }),
  ).toHaveAttribute("aria-current", "page");
  await expect(cards.first().locator("img")).toHaveAttribute(
    "src",
    /\/images\/digital-employees\/cartoon\//,
  );

  const search = page.getByPlaceholder(
    /Search name, tag, category, or capability|搜索名称、标签、分类或能力/i,
  );
  await search.fill("HSE Safety");
  await expect(cards).toHaveCount(1);
  await expect(cards).toContainText("HSE Safety Reviewer");
  await expect(pagination).toBeHidden();
  await search.fill("");
  await expect(cards).toHaveCount(6);

  const popularTags = page.getByTestId("popular-tag-filter").first();
  await expect(popularTags.getByTestId("popular-tag-option")).toHaveCount(8);
  await popularTags.getByTestId("popular-tag-toggle").click();
  await expect(popularTags.getByTestId("popular-tag-option")).toHaveCount(12);
  await popularTags.getByTestId("popular-tag-toggle").click();
  await expect(popularTags.getByTestId("popular-tag-option")).toHaveCount(8);

  await page.screenshot({
    path: testInfo.outputPath("digital-employee-directory-desktop.png"),
    fullPage: true,
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: /Filters|筛选/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Filters|筛选/i }).click();
  const filterDialog = page.getByRole("dialog");
  await expect(
    filterDialog.getByRole("heading", { name: /Filters|筛选/i }),
  ).toBeVisible();
  await expect(filterDialog.getByText(/Popular tags|常用标签/i)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(filterDialog).toBeHidden();
  await expect(cards.first()).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        Math.max(
          document.body.scrollWidth,
          document.documentElement.scrollWidth,
        ) <=
        window.innerWidth + 1,
    ),
  ).toBe(true);

  await page.screenshot({
    path: testInfo.outputPath(
      "digital-employee-directory-mobile-filters-collapsed.png",
    ),
    fullPage: true,
  });

  for (const viewport of [
    { width: 320, height: 720 },
    { width: 375, height: 812 },
    { width: 390, height: 844 },
    { width: 412, height: 915 },
    { width: 844, height: 390 },
  ]) {
    await page.setViewportSize(viewport);
    await expect(cards.first()).toBeVisible();
    expect(
      await page.evaluate(
        () =>
          Math.max(
            document.body.scrollWidth,
            document.documentElement.scrollWidth,
          ) <=
          window.innerWidth + 1,
      ),
    ).toBe(true);
  }
});

test("personal employee editor offers 24 presets, tag CRUD, custom upload, and hides inference settings", async ({
  page,
}, testInfo) => {
  mockLangGraphAPI(page, { agents: EMPLOYEES });
  await page.context().addCookies([
    {
      name: "locale",
      value: "zh-CN",
      url: String(testInfo.project.use.baseURL ?? "http://localhost:3000"),
    },
  ]);
  await page.goto("/workspace/agents");
  await page
    .getByRole("tab", { name: /My digital employees|我的数字员工/i })
    .click();

  const firstCard = page.getByTestId("digital-employee-card").first();
  await firstCard
    .getByRole("button", { name: /More actions|更多操作/i })
    .click();
  await page.getByRole("menuitem", { name: /Edit details|编辑信息/i }).click();

  await expect(
    page.getByRole("heading", { name: /Edit digital employee|编辑数字员工/i }),
  ).toBeVisible();
  await expect(page.locator('input[type="file"]')).toHaveAttribute(
    "accept",
    "image/png,image/jpeg,image/webp",
  );
  await expect(
    page.locator('button[title="勘探研究"], button[title="Exploration"]'),
  ).toHaveCount(1);
  await expect(
    page.locator("button[title]").filter({ has: page.locator("img") }),
  ).toHaveCount(24);
  await expect(page.getByTestId("digital-employee-tag-editor")).toBeVisible();
  await expect(
    page.getByText(
      /Dedicated model and inference settings|专属模型与推理参数/i,
    ),
  ).toBeHidden();

  const tagInput = page.getByRole("textbox", {
    name: /Add or edit tag|新增或编辑标签/i,
  });
  await tagInput.fill("Knowledge");
  await page.getByRole("button", { name: /Add tag|添加标签/i }).click();
  const tagEditor = page.getByTestId("digital-employee-tag-editor");
  await expect(tagEditor.getByText("Knowledge", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: /Delete tag Knowledge|删除标签 Knowledge/i })
    .click();
  await expect(tagEditor.getByText("Knowledge", { exact: true })).toBeHidden();

  await page
    .getByRole("button", { name: /Edit tag Reports|编辑标签 Reports/i })
    .click();
  await tagInput.fill("研究报告");
  await page
    .getByRole("button", {
      name: /Confirm tag edit|确认(?:修改标签|标签修改)/i,
    })
    .click();
  await expect(tagEditor.getByText("研究报告", { exact: true })).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("heading", { name: /Edit digital employee|编辑数字员工/i }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        Math.max(
          document.body.scrollWidth,
          document.documentElement.scrollWidth,
        ) <=
        window.innerWidth + 1,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("digital-employee-settings-mobile.png"),
    fullPage: true,
  });
});
