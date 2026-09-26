import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

for (const width of [1280, 390]) {
  test(`employee slash uses pinned skills without personal install at ${width}px`, async ({
    page,
  }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const team = {
      name: "reports",
      source: "team" as const,
      skills: ["latest-only"],
    };
    mockLangGraphAPI(page, {
      teamAgents: [team],
      skills: [{ name: "viewer-only", description: "Personal" }],
      threads: [
        {
          thread_id: MOCK_THREAD_ID,
          agent_name: "reports",
          metadata: { agent_source: "team" },
        },
      ],
    });
    await page.route("**/api/agents/reports?source=team", (route) =>
      route.fulfill({ json: team }),
    );
    await page.route("**/api/threads/*/employee-publication", (route) =>
      route.fulfill({
        json: {
          bound: true,
          name: "reports",
          revision: "pinned-old",
          current_revision: "new-head",
          update_available: true,
          knowledge_mode: "disabled",
        },
      }),
    );
    const seen: string[] = [];
    await page.route("**/api/agents/reports/invocation-skills?*", (route) => {
      seen.push(route.request().url());
      return route.fulfill({
        json: {
          revision: "pinned-old",
          skills: [{ name: "analysis", description: "", enabled: true }],
        },
      });
    });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(msg.text());
    });
    await page.goto(
      `/workspace/agents/reports/chats/${MOCK_THREAD_ID}?source=team`,
    );
    await expect(page).toHaveTitle("中国海油通用智能体");
    await page.locator("textarea").fill("/");
    const menu = page.getByRole("listbox", { name: "Skill suggestions" });
    await expect(
      menu.getByRole("option", { name: "/analysis", exact: true }),
    ).toBeVisible();
    await expect(menu).not.toContainText("viewer-only");
    await expect(menu).not.toContainText("latest-only");
    expect(
      seen.some(
        (url) => new URL(url).searchParams.get("thread_id") === MOCK_THREAD_ID,
      ),
    ).toBe(true);
    await page.screenshot({
      path: info.outputPath(`employee-slash-${width}.png`),
    });
    await menu.getByRole("option", { name: "/analysis", exact: true }).click();
    await expect(page.locator("[contenteditable=true]")).toBeVisible();
    expect(errors).toEqual([]);
  });
}

test("unavailable employee catalog is visible and never replaced by personal skills", async ({
  page,
}, info) => {
  const team = { name: "reports", source: "team", skills: ["analysis"] };
  mockLangGraphAPI(page, {
    teamAgents: [{ name: "reports", source: "team", skills: ["analysis"] }],
    skills: [{ name: "viewer-only", description: "Personal" }],
  });
  await page.route("**/api/agents/reports?source=team", route => route.fulfill({ json: team }));
  await page.route("**/api/agents/reports/invocation-skills?*", (route) =>
    route.fulfill({ status: 403, json: { detail: "Revoked" } }),
  );
  await page.goto("/workspace/agents/reports/chats/new?source=team");
  await page.locator("textarea").fill("/viewer");
  await expect(
    page
      .getByRole("alert")
      .filter({
        hasText: /personal skills will not be substituted|不会改用个人技能/,
      }),
  ).toBeVisible();
  await expect(page.getByRole("option", { name: /viewer-only/ })).toHaveCount(
    0,
  );
  await page.screenshot({
    path: info.outputPath("employee-slash-revoked.png"),
  });
});
