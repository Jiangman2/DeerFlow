import { expect, test, type Locator } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(150_000);
const agent = {
  name: "submit-layout",
  display_name: "附件布局测试助手",
  description: "发送后附件与知识库保持对齐。",
  system_prompt: "Synthetic layout test.",
};
const files = Array.from({ length: 6 }, (_, i) => ({
  name: `测试附件${i}.txt`,
  mimeType: "text/plain",
  buffer: Buffer.from("Synthetic layout test."),
}));

async function checkRail(composer: Locator) {
  const form = composer.locator(".cnooc-composer");
  const rail = composer.locator(".cnooc-composer-header");
  const card = rail
    .locator(".cnooc-knowledge-scope-chip, .cnooc-attachment-card")
    .first();
  await expect(card).toBeVisible();
  await rail.evaluate((el) => {
    el.scrollTop = 0;
  });
  await expect
    .poll(async () => {
      const r = (await rail.boundingBox())!,
        c = (await card.boundingBox())!;
      return Math.abs(c.x - r.x);
    })
    .toBeLessThanOrEqual(1);
  const f = (await form.boundingBox())!,
    r = (await rail.boundingBox())!;
  expect(r.x - f.x).toBeGreaterThanOrEqual(11);
  expect(r.x - f.x).toBeLessThanOrEqual(20);
  expect(f.x + f.width - r.x - r.width).toBeGreaterThanOrEqual(11);
  expect(r.y - f.y).toBeGreaterThanOrEqual(11);
  expect(r.height).toBeLessThanOrEqual(49);
  const text = (await composer.locator("textarea").boundingBox())!;
  expect(text.y).toBeGreaterThanOrEqual(r.y + r.height - 1);
  expect(text.height).toBeGreaterThanOrEqual(72);
}

for (const width of [1440, 390]) {
  for (const employee of [false, true]) {
    for (const material of ["knowledge", "uploads", "mixed"]) {
      test(`submit ${employee ? "employee" : "main"} ${material} ${width}`, async ({
        page,
        baseURL,
      }, info) => {
        await page.setViewportSize({ width, height: 900 });
        await page
          .context()
          .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
        mockLangGraphAPI(page, { agents: [agent] });
        await page.route("**/api/runtime-config", (route) =>
          route.fulfill({
            json: { skillHubEnabled: false, weknoraFrontendUrl: null },
          }),
        );
        await page.route("**/api/weknora/knowledge-bases", (route) =>
          route.fulfill({
            json: {
              items: [
                {
                  id: "kb-submit",
                  name: "测试制度知识库",
                  type: "document",
                  knowledge_count: 1,
                },
              ],
              total: 1,
            },
          }),
        );
        await page.route("**/api/weknora/knowledge-bases/*/tags*", (route) =>
          route.fulfill({ json: { items: [], total: 0 } }),
        );
        await page.route("**/api/threads/*/uploads", (route) =>
          route.fulfill({
            json: {
              success: true,
              skipped_files: [],
              message: "ok",
              files: files.map((file) => ({
                filename: file.name,
                size: file.buffer.length,
                path: `/tmp/${file.name}`,
                virtual_path: `/mnt/user-data/uploads/${file.name}`,
                artifact_url: `/api/test/${file.name}`,
              })),
            },
          }),
        );
        let releaseStream!: () => void;
        const streamGate = new Promise<void>((resolve) => {
          releaseStream = resolve;
        });
        let submitted: Record<string, unknown> | undefined;
        await page.route(
          "**/api/langgraph/threads/*/runs/stream",
          async (route) => {
            submitted = route.request().postDataJSON() as Record<
              string,
              unknown
            >;
            await streamGate;
            await route.fallback();
          },
        );
        const errors: string[] = [];
        page.on("pageerror", (error) => errors.push(error.message));
        await page.goto(
          employee
            ? `/workspace/agents/${agent.name}/chats/new`
            : "/workspace/chats/new",
          { waitUntil: "domcontentloaded" },
        );
        const welcome = page.getByTestId("welcome-composer");
        await expect(welcome.locator("textarea")).toBeEditable({
          timeout: 90_000,
        });
        if (material !== "uploads") {
          await welcome.getByTestId("knowledge-scope-trigger").click();
          await page.getByTestId("knowledge-scope-group-kb").click();
          await page.getByTestId("knowledge-base-item-kb-submit").click();
          await page.keyboard.press("Escape");
          await expect(
            welcome.locator(".cnooc-knowledge-scope-chip"),
          ).toHaveCount(1);
        }
        if (material !== "knowledge")
          await welcome.locator('input[type="file"]').setInputFiles(files);
        const draft = "请概括制度要求。\n请按主题整理。\n注明参考来源。";
        await welcome.locator("textarea").fill(draft);
        await welcome.locator("textarea").press("Enter");
        const chat = page.getByTestId("chat-composer");
        try {
          await expect
            .poll(() => Boolean(submitted), { timeout: 30_000 })
            .toBe(true);
          await expect(chat).toBeVisible();
          if (material !== "uploads") await checkRail(chat);
          await page.screenshot({ path: info.outputPath("sending.png") });
        } finally {
          releaseStream();
        }
        await expect(
          page.getByText("Hello from DeerFlow!", { exact: false }).first(),
        ).toBeVisible({ timeout: 30_000 });
        await expect(chat.locator("textarea")).toBeEditable();
        // Uploaded files clear after successful submission; persistent scope and
        // newly added follow-up attachments must both use the actual chat layout.
        if (material !== "knowledge")
          await chat.locator('input[type="file"]').setInputFiles(files);
        const followup = Array.from(
          { length: 12 },
          (_, i) => `第${i + 1}行：补充问题，文字区域独立滚动。`,
        ).join("\n");
        await chat.locator("textarea").fill(followup);
        await checkRail(chat);
        const rail = chat.locator(".cnooc-composer-header");
        if (material !== "knowledge") {
          expect(
            await rail.evaluate((el) => el.scrollHeight > el.clientHeight),
          ).toBe(true);
          await rail.evaluate((el) => {
            el.scrollTop = el.scrollHeight;
          });
          await chat.locator(".cnooc-attachment-card button").last().click();
          await expect(chat.locator(".cnooc-attachment-card")).toHaveCount(5);
        }
        await expect(chat.locator("textarea")).toHaveValue(followup);
        expect(
          await chat
            .locator("textarea")
            .evaluate((el) => el.scrollHeight > el.clientHeight),
        ).toBe(true);
        await rail.evaluate((el) => {
          el.scrollTop = 0;
        });
        await page.screenshot({ path: info.outputPath("after-send.png") });
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth),
        ).toBeLessThanOrEqual(width);
        expect(errors).toEqual([]);
      });
    }
  }
}
