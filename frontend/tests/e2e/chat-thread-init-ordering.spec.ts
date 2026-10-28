import { expect, test } from "@playwright/test";

import { handleRunStream, mockLangGraphAPI } from "./utils/mock-api";

/**
 * Regression for https://github.com/bytedance/deer-flow/issues/2746.
 *
 * On a brand-new chat, the LangGraph SDK's useStream eagerly fetches
 * `/threads/{id}/history` the moment it receives a thread id, while other
 * frontend hydration paths may request `/threads/{id}/state` or runs for the
 * same reason. All endpoints assume the thread already exists on the backend;
 * if the frontend forwards the id before thread creation succeeds, they 404.
 * The released upload flow explicitly creates the thread before submitting
 * its run. Reads after that acknowledgement are valid even before streaming.
 */
test.describe("Chat: thread API request ordering on first send", () => {
  test("does not read thread data before explicit creation succeeds", async ({
    page,
  }) => {
    type EventLog = {
      phase: "sent" | "response" | "done";
      url: string;
      method: string;
      seq: number;
      status?: number;
    };
    const events: EventLog[] = [];
    // Monotonic sequence number — Date.now() is millisecond-resolution and
    // would let two requests share a timestamp, which would defeat the
    // strict-ordering assertions below.
    let nextSeq = 0;

    page.on("request", (req) => {
      events.push({
        phase: "sent",
        url: req.url(),
        method: req.method(),
        seq: nextSeq++,
      });
    });
    page.on("requestfinished", (req) => {
      events.push({
        phase: "done",
        url: req.url(),
        method: req.method(),
        seq: nextSeq++,
      });
    });
    page.on("response", (response) => {
      events.push({
        phase: "response",
        url: response.url(),
        method: response.request().method(),
        status: response.status(),
        seq: nextSeq++,
      });
    });

    mockLangGraphAPI(page);

    // Widen the real pre-create race, not the already-created/pre-stream gap.
    await page.route("**/api/langgraph/threads", async (route) => {
      if (route.request().method() === "POST") {
        await new Promise((resolve) => setTimeout(resolve, 250));
      }
      return route.fallback();
    });

    // Slow down /runs/stream so any pre-create /history or /runs request
    // would land well before the stream returns metadata, widening the
    // race window the bug used to exploit.
    await page.route(
      "**/api/langgraph/threads/*/runs/stream",
      async (route) => {
        await new Promise((r) => setTimeout(r, 250));
        return handleRunStream(route);
      },
    );
    await page.route("**/api/langgraph/runs/stream", async (route) => {
      await new Promise((r) => setTimeout(r, 250));
      return handleRunStream(route);
    });

    await page.goto("/workspace/chats/new");

    const textarea = page.getByPlaceholder(/how can i assist you/i);
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill("Hello");
    await textarea.press("Enter");

    // Wait for streaming response so all init requests have a chance to fire.
    await expect(page.getByText("Hello from DeerFlow!")).toBeVisible({
      timeout: 15_000,
    });

    const isHistory = (url: string) =>
      /\/api\/langgraph\/threads\/[^/]+\/history/.test(url);
    const isState = (url: string, method: string) =>
      method === "GET" &&
      /\/api\/langgraph\/threads\/[^/]+\/state(?:\?|$)/.test(url);
    const isRunsList = (url: string, method: string) =>
      method === "GET" &&
      /\/api\/langgraph\/threads\/[^/]+\/runs(\?|$)/.test(url);
    const isRunsStream = (url: string, method: string) =>
      method === "POST" && /\/runs\/stream(\?|$)/.test(url);

    const runsStreamSent = events.find(
      (e) => e.phase === "sent" && isRunsStream(e.url, e.method),
    );
    expect(
      runsStreamSent,
      "Expected POST /runs/stream to be issued during send",
    ).toBeDefined();
    const created = events.find(
      (e) =>
        e.phase === "response" &&
        e.method === "POST" &&
        /\/api\/langgraph\/threads(?:\?|$)/.test(e.url) &&
        e.status !== undefined &&
        e.status >= 200 &&
        e.status < 300,
    );
    expect(
      created,
      "Expected a successful thread creation response",
    ).toBeDefined();
    expect(created!.seq).toBeLessThan(runsStreamSent!.seq);

    const earlyHistory = events.filter(
      (e) => e.phase === "sent" && isHistory(e.url) && e.seq < created!.seq,
    );
    const earlyRunsList = events.filter(
      (e) =>
        e.phase === "sent" &&
        isRunsList(e.url, e.method) &&
        e.seq < created!.seq,
    );
    const earlyState = events.filter(
      (e) =>
        e.phase === "sent" && isState(e.url, e.method) && e.seq < created!.seq,
    );

    expect(
      earlyHistory.map((e) => e.url),
      "GET /history must not precede thread creation — see issue #2746",
    ).toEqual([]);
    expect(
      earlyRunsList.map((e) => e.url),
      "GET /runs must not precede thread creation — see issue #2746",
    ).toEqual([]);
    expect(earlyState.map((e) => e.url)).toEqual([]);
  });
});
