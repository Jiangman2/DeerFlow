import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test.describe.configure({ mode: "serial" });

test("scheduled tasks page is reachable from sidebar", async ({ page }) => {
  mockLangGraphAPI(page, {
    threads: [],
    scheduledTasks: [
      {
        id: "task-1",
        thread_id: "thread-1",
        title: "Daily summary",
        prompt: "Summarize thread",
        schedule_type: "cron",
        schedule_spec: { cron: "0 9 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-07-02T01:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
    ],
  });

  await page.goto("/workspace/chats/new");
  await page.getByRole("link", { name: /scheduled tasks/i }).click();
  await page.waitForURL("**/workspace/scheduled-tasks");
  await expect(page).toHaveURL(/workspace\/scheduled-tasks/);
  await expect(
    page.getByRole("button", { name: /Daily summary/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Daily summary/i }).click();
  await expect(page.getByTestId("scheduled-task-runs")).toContainText("0 runs");
});

test("thread page links to filtered scheduled tasks", async ({ page }) => {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Thread with schedules",
        updated_at: "2025-06-01T12:00:00Z",
      },
    ],
    scheduledTasks: [
      {
        id: "task-1",
        thread_id: MOCK_THREAD_ID,
        title: "Thread task",
        prompt: "Summarize thread",
        schedule_type: "cron",
        schedule_spec: { cron: "0 9 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-07-02T01:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
    ],
  });

  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  await page
    .locator("header")
    .getByRole("link", { name: /scheduled tasks/i })
    .click();
  await page.waitForURL(new RegExp(`thread_id=${MOCK_THREAD_ID}`));
});

test("user can create a scheduled task from the page", async ({ page }) => {
  mockLangGraphAPI(page, { threads: [], scheduledTasks: [] });
  await page.route("**/api/models", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        models: [
          {
            id: "qwen",
            name: "qwen-bailian",
            model: "qwen3.7-plus",
            display_name: "Qwen 3.7 Plus",
          },
          {
            id: "deepseek",
            name: "deepseek-v4-flash",
            model: "DeepSeek-V4-Flash",
            display_name: "DeepSeek V4 Flash",
          },
        ],
        token_usage: { enabled: false },
      }),
    }),
  );

  await page.goto("/workspace/scheduled-tasks");
  await page
    .locator("header")
    .getByRole("button", { name: "Create scheduled task" })
    .click();
  const createForm = page.getByTestId("scheduled-task-create-form");
  await expect(createForm.getByTestId("scheduled-task-model")).toContainText(
    "DeepSeek V4 Flash",
  );
  await createForm.getByRole("button", { name: "One-time" }).click();
  await createForm.getByLabel("Run at").fill("2026-07-02T09:00");
  await createForm.getByPlaceholder("Task title").fill("Created from UI");
  await createForm.getByPlaceholder("Prompt").fill("Summarize thread");
  await createForm.getByRole("button", { name: "Create" }).click();
  await expect(
    page.getByRole("button", { name: /Created from UI/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Created from UI/i }).click();
  await expect(
    page.getByTestId("scheduled-task-detail").getByText("Summarize thread"),
  ).toBeVisible();
  await expect(
    page.getByTestId("scheduled-task-detail").getByText("DeepSeek V4 Flash"),
  ).toBeVisible();
});

test("user can create a fixed-interval task", async ({ page }) => {
  let createBody: Record<string, unknown> | null = null;
  mockLangGraphAPI(page, { threads: [], scheduledTasks: [] });
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith("/api/scheduled-tasks")
    ) {
      createBody = request.postDataJSON() as Record<string, unknown>;
    }
  });

  await page.goto("/workspace/scheduled-tasks");
  await page
    .locator("header")
    .getByRole("button", { name: "Create scheduled task" })
    .click();
  const createForm = page.getByTestId("scheduled-task-create-form");
  await createForm.getByRole("button", { name: "Interval" }).click();
  await createForm.getByLabel("Repeat every").fill("90");
  await createForm.getByPlaceholder("Task title").fill("Interval health check");
  await createForm.getByPlaceholder("Prompt").fill("Check system health");
  if (process.env.SCHEDULED_TASK_QA_SCREENSHOT_INTERVAL) {
    await page.screenshot({
      path: process.env.SCHEDULED_TASK_QA_SCREENSHOT_INTERVAL,
      fullPage: false,
    });
  }
  await createForm.getByRole("button", { name: "Create" }).click();

  await expect(
    page.getByRole("button", { name: /Interval health check/i }),
  ).toBeVisible();
  expect(createBody).toMatchObject({
    assistant_id: "lead_agent",
    schedule_type: "interval",
    schedule_spec: { every_seconds: 5400 },
  });
});

test("duplicate pre-fills a new task without creating it", async ({ page }) => {
  let createRequests = 0;
  mockLangGraphAPI(page, {
    threads: [],
    scheduledTasks: [
      {
        id: "task-copy-source",
        thread_id: "thread-copy-source",
        context_mode: "reuse_thread",
        assistant_id: "research-bot",
        title: "Daily summary",
        prompt: "Summarize this conversation",
        schedule_type: "cron",
        schedule_spec: { cron: "0 18 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-08-28T18:00:00Z",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-08-01T00:00:00Z",
        updated_at: "2026-08-01T00:00:00Z",
      },
    ],
  });
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname.endsWith("/api/scheduled-tasks")
    ) {
      createRequests += 1;
    }
  });

  await page.goto("/workspace/scheduled-tasks");
  await page.getByRole("button", { name: /Daily summary/i }).click();
  await page
    .getByTestId("scheduled-task-detail")
    .getByRole("button", { name: "Duplicate" })
    .click();

  const createForm = page.getByTestId("scheduled-task-create-form");
  await expect(createForm.getByPlaceholder("Task title")).toHaveValue(
    "Daily summary (Copy)",
  );
  await expect(createForm.getByPlaceholder("Prompt")).toHaveValue(
    "Summarize this conversation",
  );
  await expect(createForm.getByPlaceholder("Thread ID")).toHaveValue(
    "thread-copy-source",
  );
  await expect(
    createForm.getByTestId("scheduled-task-create-agent"),
  ).toContainText("research-bot");
  await expect(createForm.getByTestId("schedule-preview")).toHaveText(
    "Every day at 18:00 (UTC)",
  );
  if (process.env.SCHEDULED_TASK_QA_SCREENSHOT_DUPLICATE) {
    await page.screenshot({
      path: process.env.SCHEDULED_TASK_QA_SCREENSHOT_DUPLICATE,
      fullPage: false,
    });
  }
  expect(createRequests).toBe(0);
});

test("create form keeps the schedule preview above its action bar", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1510, height: 829 });
  mockLangGraphAPI(page, { threads: [], scheduledTasks: [] });

  await page.goto("/workspace/scheduled-tasks");
  await page
    .locator("header")
    .getByRole("button", { name: "Create scheduled task" })
    .click();

  const scrollArea = page.getByTestId("scheduled-task-create-scroll");
  const preview = page.getByTestId("schedule-preview");
  const actions = page.getByTestId("scheduled-task-create-actions");

  await scrollArea.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });

  await expect(preview).toBeVisible();
  await expect(actions).toBeVisible();

  const previewBox = await preview.boundingBox();
  const actionsBox = await actions.boundingBox();
  expect(previewBox).not.toBeNull();
  expect(actionsBox).not.toBeNull();
  expect((previewBox?.y ?? 0) + (previewBox?.height ?? 0)).toBeLessThanOrEqual(
    actionsBox?.y ?? 0,
  );
});

test("user can pause a scheduled task from the detail pane", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [],
    scheduledTasks: [
      {
        id: "task-1",
        thread_id: "thread-1",
        title: "Pausable task",
        prompt: "Summarize thread",
        schedule_type: "cron",
        schedule_spec: { cron: "0 9 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-07-02T01:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
    ],
  });

  await page.goto("/workspace/scheduled-tasks");
  await page.getByRole("button", { name: /Pausable task/i }).click();
  const detail = page.getByTestId("scheduled-task-detail");
  await detail.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByTestId("scheduled-task-item-task-1")).toBeVisible();
  await expect(
    page
      .getByTestId("scheduled-task-item-task-1")
      .getByText("Paused", { exact: true }),
  ).toBeVisible();
});

test("trigger shows a run entry in the detail pane", async ({ page }) => {
  mockLangGraphAPI(page, {
    threads: [],
    scheduledTasks: [
      {
        id: "task-1",
        thread_id: "thread-1",
        title: "Triggerable task",
        prompt: "Summarize thread",
        schedule_type: "cron",
        schedule_spec: { cron: "0 9 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-07-02T01:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
    ],
  });

  await page.goto("/workspace/scheduled-tasks");
  await page.getByRole("button", { name: "Trigger now" }).click();
  await page.getByRole("button", { name: /Triggerable task/i }).click();
  await expect(page.getByTestId("scheduled-task-runs")).toContainText("1 run");
  await expect(
    page.getByTestId("scheduled-task-run-list").getByText(/Manual · Success/i),
  ).toBeVisible();
});

test("detail pane falls back to a visible task after filters hide the selected task", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    threads: [],
    scheduledTasks: [
      {
        id: "task-enabled",
        thread_id: "thread-1",
        title: "Enabled task",
        prompt: "Enabled prompt",
        schedule_type: "cron",
        schedule_spec: { cron: "0 9 * * *" },
        timezone: "UTC",
        status: "enabled",
        next_run_at: "2026-07-02T01:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
      {
        id: "task-paused",
        thread_id: "thread-2",
        title: "Paused task",
        prompt: "Paused prompt",
        schedule_type: "cron",
        schedule_spec: { cron: "0 10 * * *" },
        timezone: "UTC",
        status: "paused",
        next_run_at: "2026-07-02T02:00:00+00:00",
        last_run_at: null,
        last_run_id: null,
        last_error: null,
        run_count: 0,
        created_at: "2026-07-01T00:00:00+00:00",
        updated_at: "2026-07-01T00:00:00+00:00",
      },
    ],
  });

  await page.goto("/workspace/scheduled-tasks");
  await page.getByRole("button", { name: /Paused task/i }).click();
  await expect(
    page.getByTestId("scheduled-task-detail").getByText("Paused task"),
  ).toBeVisible();
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: "Enabled", exact: true }).click();

  await page.getByRole("button", { name: /Enabled task/i }).click();
  await expect(
    page.getByTestId("scheduled-task-detail").getByText("Enabled task"),
  ).toBeVisible();
  await expect(
    page.getByTestId("scheduled-task-item-task-enabled"),
  ).toBeVisible();
  await expect(page.getByTestId("scheduled-task-item-task-paused")).toHaveCount(
    0,
  );
});
