import { expect, test } from "@playwright/test";

import { handleRunStream, mockLangGraphAPI, MOCK_RUN_ID } from "./utils/mock-api";

for (const revision of ["draft-created", null]) {
  test(`bootstrap form save requires the freshly read personal draft: ${revision ?? "missing"}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: revision ? 1280 : 390, height: 900 });
    mockLangGraphAPI(page, { skills: [{ name: "report", description: "Reports", enabled: true, category: "custom" }] });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      // The negative case deliberately reports its rejected form save.
      if (message.type() === "error" && !(revision === null && message.text().startsWith("Failed to save employee creation form"))) errors.push(message.text());
    });
    let streamCount = 0;
    let privateReads = 0;
    const writes: Record<string, unknown>[] = [];
    await page.route("**/api/agents/check?*", (route) => route.fulfill({ json: { name: "report-helper", available: true } }));
    await page.route(/\/api\/agents\/report-helper(\?|$)/, (route) => {
      if (route.request().method() === "GET") {
        expect(new URL(route.request().url()).searchParams.get("source")).toBe("personal");
        privateReads += 1;
        return route.fulfill({ json: { name: "report-helper", description: "Reports", source: "personal", draft_revision: revision } });
      }
      const body = route.request().postDataJSON() as Record<string, unknown>;
      writes.push(body);
      // Match the real API precondition, rather than accepting incomplete mocks.
      if (body.expected_draft_revision !== "draft-created") return route.fulfill({ status: 428, json: { detail: "Draft revision required" } });
      return route.fulfill({ json: { name: "report-helper", description: "Reports", ...body, draft_revision: "draft-saved" } });
    });
    await page.route("**/api/langgraph/threads/*/runs/stream", async (route) => {
      streamCount += 1;
      if (streamCount === 1) return handleRunStream(route);
      const threadId = new URL(route.request().url()).pathname.split("/").at(-3)!;
      const events = [
        { event: "metadata", data: { run_id: MOCK_RUN_ID, thread_id: threadId } },
        { event: "events", data: { event: "on_tool_end", name: "setup_agent", run_id: "setup-receipt", data: { output: "Employee saved" }, tags: [], metadata: {} } },
        { event: "end", data: {} },
      ];
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: events.map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event.data)}\n\n`).join("") });
    });
    await page.goto("/workspace/agents/new");
    await expect(page).toHaveURL(/\/workspace\/agents\/new$/);
    await expect(page).toHaveTitle(/中国海油通用智能体$/);
    const selector = page.getByTestId("agent-skill-selection");
    await selector.getByRole("radio", { name: "Choose explicitly" }).click();
    await selector.getByRole("checkbox", { name: /^report/ }).check();
    await page.locator('input[maxlength="64"]').fill("report-helper");
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.getByRole("button", { name: "More actions", exact: true }).click();
    const save = page.getByRole("menuitem", { name: "Save digital employee", exact: true });
    await expect(save).toBeEnabled();
    await save.click();
    await expect.poll(() => privateReads).toBe(1);
    if (revision) {
      await expect.poll(() => writes.length).toBe(1);
      expect(writes[0]).toMatchObject({ expected_draft_revision: revision, skills: ["report"], knowledge_scope: null });
      await expect(page.getByRole("button", { name: /Start work/ })).toBeVisible();
    } else {
      await expect(page.getByText(/form settings could not be saved/)).toBeVisible();
      expect(writes).toEqual([]);
      await expect(page.getByRole("button", { name: /Start work/ })).toHaveCount(0);
    }
    await expect(page.locator("[data-nextjs-dialog-overlay]")).toHaveCount(0);
    expect(errors).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("bootstrap-save.png"), animations: "disabled" });
  });
}
