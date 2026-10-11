import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test("square retry refreshes the failed team list as well as its settings", async ({ page }) => {
  mockLangGraphAPI(page);
  let recovered = false;
  let teamRequests = 0;
  let settingsRequests = 0;
  await page.route("**/api/team-agents*", (route) => {
    teamRequests += 1;
    return route.fulfill({
      status: recovered ? 200 : 503,
      contentType: "application/json",
      body: JSON.stringify(recovered ? { agents: [] } : { detail: "Synthetic unavailable" }),
    });
  });
  await page.route("**/api/digital-employee-square/settings", (route) => {
    settingsRequests += 1;
    return route.fulfill({ contentType: "application/json", body: JSON.stringify({ items: [] }) });
  });
  await page.goto("/workspace/agents");
  const retry = page.getByTestId("square-retry");
  await expect(retry).toBeVisible({ timeout: 15_000 });
  const beforeTeam = teamRequests;
  const beforeSettings = settingsRequests;
  recovered = true;
  await retry.click();
  await expect.poll(() => teamRequests).toBeGreaterThan(beforeTeam);
  await expect.poll(() => settingsRequests).toBeGreaterThan(beforeSettings);
  await expect(retry).toBeHidden();
});
