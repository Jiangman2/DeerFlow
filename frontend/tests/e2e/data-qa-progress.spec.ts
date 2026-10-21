import { createServer, type ServerResponse } from "node:http";

import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const THREAD = "00000000-0000-0000-0000-000000009121";
const RUN = "00000000-0000-0000-0000-000000009122";
const messages = [
  { type: "human", id: "human-data-qa", content: "中海油有多少口井" },
  {
    type: "ai",
    id: "ai-data-qa",
    content: "",
    tool_calls: [
      {
        id: "qa-call",
        name: "datalake_data_qa",
        args: { query: "中海油有多少口井" },
      },
    ],
  },
];

for (const width of [1440, 390]) {
  test(`Data QA shows live progress before result at ${width}px`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 900 });
    await page
      .context()
      .addCookies([
        {
          name: "locale",
          value: "zh-CN",
          url: String(testInfo.project.use.baseURL),
        },
      ]);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    const consoleMessages: { level: string; text: string }[] = [];
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type()))
        consoleMessages.push({ level: message.type(), text: message.text() });
    });
    const timeline: {
      stage: string;
      observedAt: string;
      streamOpen: boolean;
    }[] = [];
    function observed(stage: string) {
      timeline.push({
        stage,
        observedAt: new Date().toISOString(),
        streamOpen: Boolean(response && !response.writableEnded),
      });
    }
    mockLangGraphAPI(page, {
      threads: [{ thread_id: THREAD, title: "问数进度验收", messages: [] }],
    });
    let response: ServerResponse | undefined;
    const server = createServer((req, res) => {
      res.setHeader("Access-Control-Allow-Origin", req.headers.origin ?? "*");
      res.setHeader("Access-Control-Allow-Credentials", "true");
      res.setHeader("Access-Control-Expose-Headers", "Content-Location");
      res.setHeader("Content-Location", `/threads/${THREAD}/runs/${RUN}`);
      res.setHeader(
        "Access-Control-Allow-Headers",
        req.headers["access-control-request-headers"] ?? "*",
      );
      res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
      if (req.method === "OPTIONS") {
        res.writeHead(204);
        res.end();
        return;
      }
      req.resume();
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
      });
      res.flushHeaders();
      response = res;
      send("metadata", { run_id: RUN, thread_id: THREAD });
      send("values", { messages });
    });
    function send(event: string, data: unknown) {
      response?.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
    }
    function progress(stage: string, sequence: number) {
      send("custom", {
        type: "data_qa_progress",
        thread_id: THREAD,
        run_id: RUN,
        tool_call_id: "qa-call",
        tool: "datalake_data_qa",
        stage,
        sequence,
      });
    }
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const address = server.address();
    if (!address || typeof address === "string")
      throw new Error("Missing fixture port");
    await page.route("**/api/langgraph/threads/*/runs/stream", (route) =>
      route.continue({ url: `http://127.0.0.1:${address.port}/stream` }),
    );
    try {
      await page.goto(`/workspace/chats/${THREAD}`);
      const input = page.locator('textarea[name="message"]');
      await expect(input).toBeVisible({ timeout: 60_000 });
      await expect(page).toHaveURL(new RegExp(`/workspace/chats/${THREAD}$`));
      await expect(page).not.toHaveTitle("");
      await input.fill("中海油有多少口井");
      await input.press("Enter");
      await expect
        .poll(() => Boolean(response), { timeout: 20_000 })
        .toBe(true);
      await expect(
        page.getByRole("status").filter({ hasText: "等待查询进展" }),
      ).toBeVisible();
      observed("waiting");
      progress("waiting", 1);
      progress("processing", 2);
      await expect(
        page.getByRole("status").filter({ hasText: "正在处理您的查询" }),
      ).toBeVisible();
      observed("processing");
      expect(response!.writableEnded).toBe(false);
      progress("answering", 3);
      await expect(
        page.getByRole("status").filter({ hasText: "正在整理问数回答" }),
      ).toBeVisible();
      observed("answering");
      await page.getByText("查看问数进展", { exact: true }).click();
      await expect(page.locator("details[open] li")).toHaveCount(3);
      const bounds = await page
        .getByText("查看问数进展", { exact: true })
        .boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      if (process.env.DATA_QA_SCREENSHOT_DIR)
        await page.screenshot({
          path: `${process.env.DATA_QA_SCREENSHOT_DIR}/data-qa-live-${width}.png`,
        });
      progress("completed", 4);
      await expect(
        page.getByRole("status").filter({ hasText: "智能问数调用完成" }),
      ).toBeVisible();
      observed("completed");
      // Keep the assistant stream open: the tool spinner must already stop.
      expect(response!.writableEnded).toBe(false);
      await expect(
        page
          .locator('[role="status"]')
          .filter({ hasText: "智能问数调用完成" })
          .locator("../../..")
          .locator(".animate-spin"),
      ).toHaveCount(0);
      if (process.env.DATA_QA_SCREENSHOT_DIR)
        await page.screenshot({
          path: `${process.env.DATA_QA_SCREENSHOT_DIR}/data-qa-done-${width}.png`,
        });
      expect(pageErrors).toEqual([]);
      await testInfo.attach("live-stream-evidence", {
        body: JSON.stringify(
          {
            url: page.url(),
            title: await page.title(),
            width,
            timeline,
            pageErrors,
            consoleMessages,
          },
          null,
          2,
        ),
        contentType: "application/json",
      });
    } finally {
      response?.end();
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
}
