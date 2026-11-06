import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const THREAD = "00000000-0000-0000-0000-000000009411";

for (const trigger of ["periodic", "focus"] as const) {
  test(`recovers a half-open stream via ${trigger}, hydrates results and permits the next run`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const row = {
      thread_id: THREAD,
      title: "Stream recovery",
      messages: [] as unknown[],
      artifacts: [] as string[],
    };
    mockLangGraphAPI(page, { threads: [row] });
    let finished = false;
    let statusReads = 0;
    const cancellations: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/cancel")) cancellations.push(request.url());
    });
    await page.route(`**/threads/${THREAD}/runs/*/cancel*`, (route) =>
      route.fulfill({ json: {} }),
    );
    await page.route(`**/threads/${THREAD}/runs/reconcile-*`, (route) => {
      statusReads++;
      return route.fulfill({
        json: {
          thread_id: THREAD,
          run_id: route.request().url().split("/").at(-1),
          status: finished ? "success" : "running",
        },
      });
    });
    // Keep the actual browser ReadableStream open forever after metadata. This
    // reproduces a lost terminal frame without replacing useStream or its UI.
    await page.addInitScript(
      ({ thread }) => {
        const original = window.fetch.bind(window);
        let count = 0;
        window.fetch = async (input, init) => {
          const url = String(input instanceof Request ? input.url : input);
          if (!url.endsWith(`/threads/${thread}/runs/stream`))
            return original(input, init);
          count++;
          const run = `reconcile-${count}`;
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: metadata\ndata: ${JSON.stringify({ thread_id: thread, run_id: run })}\n\n`,
                  ),
                );
                init?.signal?.addEventListener(
                  "abort",
                  () =>
                    controller.error(new DOMException("Aborted", "AbortError")),
                  { once: true },
                );
              },
            }),
            {
              headers: {
                "Content-Type": "text/event-stream",
                "Content-Location": `/threads/${thread}/runs/${run}`,
              },
            },
          );
        };
      },
      { thread: THREAD },
    );
    await page.goto(`/workspace/chats/${THREAD}`);
    const input = page.getByPlaceholder(/how can i assist you/i);
    await input.fill("Please create the report");
    await input.press("Enter");
    const thinking = page.getByText(/Thinking\.\.\./);
    await expect(thinking).toBeVisible();
    // A visible/focused page whose run is still active must remain busy.
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => statusReads).toBeGreaterThan(0);
    await expect(thinking).toBeVisible();
    row.messages = [
      { id: "human-final", type: "human", content: "Please create the report" },
      {
        id: "answer-final",
        type: "ai",
        content: "Authoritative final report is ready.",
      },
    ];
    row.artifacts = ["/mnt/user-data/outputs/reconciled-report.txt"];
    finished = true;
    if (trigger === "focus")
      await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect(
      page.getByText("Authoritative final report is ready.", { exact: true }),
    ).toBeVisible({ timeout: 25_000 });
    await expect(thinking).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: /artifacts/i }),
    ).toBeVisible();
    expect(cancellations).toEqual([]);

    // A second run remains independent; completing the first must not cancel
    // or immediately settle this stream through an old timer/callback.
    finished = false;
    await input.fill("Continue with another report");
    await input.press("Enter");
    await expect(thinking).toBeVisible();
    const before = statusReads;
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await expect.poll(() => statusReads).toBeGreaterThan(before);
    await expect(thinking).toBeVisible();
    expect(cancellations).toEqual([]);
    // Explicit user Stop retains its cancellation semantics.
    await page.getByRole("button", { name: "Submit", exact: true }).click();
    await expect.poll(() => cancellations.length).toBe(1);
    expect(cancellations[0]).toContain("/runs/reconcile-2/cancel");
    await expect(thinking).not.toBeVisible();
  });
}
