import { expect, test, type Locator } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(120_000);

async function ordered(above: Locator, below: Locator) {
  await expect
    .poll(async () => {
      const a = await above.boundingBox();
      const b = await below.boundingBox();
      return a && b ? b.y - a.y - a.height : -1;
    })
    .toBeGreaterThanOrEqual(-1);
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 390, height: 844 },
  { width: 320, height: 740 },
]) {
  for (const existing of [false, true]) {
    test(`main ${existing ? "existing" : "welcome"} attachments ${viewport.width}x${viewport.height}`, async ({
      page,
      baseURL,
    }, info) => {
      await page.setViewportSize(viewport);
      await page
        .context()
        .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
      mockLangGraphAPI(page, {
        threads: [
          {
            thread_id: MOCK_THREAD_ID,
            messages: [
              { type: "human", id: "qa-human", content: "合成测试消息" },
              { type: "ai", id: "qa-ai", content: "合成测试回复" },
            ],
          },
        ],
      });
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
                id: "kb-qa",
                name: "合成资料库",
                type: "document",
                knowledge_count: 19,
              },
            ],
            total: 1,
          },
        }),
      );
      await page.route("**/api/weknora/knowledge/search?*", (route) =>
        route.fulfill({
          json: {
            items: Array.from({ length: 19 }, (_, i) => ({
              id: `doc-${i}`,
              knowledge_base_id: "kb-qa",
              title: `合成知识库附件-${i}-长文件名布局验证.docx`,
              file_type: "docx",
              parse_status: "completed",
            })),
            total: 19,
            has_more: false,
          },
        }),
      );
      await page.route("**/api/weknora/knowledge-bases/*/tags*", (route) =>
        route.fulfill({ json: { items: [], total: 0 } }),
      );
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.goto(`/workspace/chats/${existing ? MOCK_THREAD_ID : "new"}`, {
        waitUntil: "domcontentloaded",
      });
      const composer = page.getByTestId(
        existing ? "chat-composer" : "welcome-composer",
      );
      const header = composer.locator(".cnooc-composer-header");
      const input = composer.locator("textarea");
      const draft = Array.from(
        { length: 15 },
        (_, i) => `第${i + 1}行：请分析这些附件，输入的文字不应被遮挡。`,
      ).join("\n");
      await expect(input).toBeEditable({ timeout: 60000 });
      await input.fill(draft);
      await composer.getByTestId("knowledge-scope-trigger").click();
      await page.getByTestId("knowledge-scope-group-file").click();
      await page.getByTestId("knowledge-document-kb-qa-doc-0").click();
      await page.keyboard.press("Escape");
      await expect(
        page.getByTestId("knowledge-document-kb-qa-doc-0"),
      ).toBeHidden();
      const height = (await composer.boundingBox())!.height;
      await composer.getByTestId("knowledge-scope-trigger").click();
      await page.getByTestId("knowledge-scope-group-file").click();
      for (let i = 1; i < 19; i++)
        await page.getByTestId(`knowledge-document-kb-qa-doc-${i}`).click();
      await page.keyboard.press("Escape");
      await expect(composer.locator(".cnooc-knowledge-scope-chip")).toHaveCount(
        19,
      );
      const textDimensions = await input.evaluate((el) => ({
        height: el.clientHeight,
        scroll: el.scrollHeight,
      }));
      expect(textDimensions.height).toBeGreaterThanOrEqual(72);
      expect(textDimensions.scroll).toBeGreaterThan(textDimensions.height);
      await input.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      expect(await input.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
      await input.evaluate((el) => {
        el.scrollTop = 0;
      });
      await expect
        .poll(async () => (await composer.boundingBox())!.height)
        .toBeLessThanOrEqual(height + 1);
      expect((await header.boundingBox())!.height).toBeLessThanOrEqual(48);
      await ordered(header, composer.locator(".cnooc-composer-input"));
      await header.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      await expect(
        composer.locator(".cnooc-knowledge-scope-chip").last(),
      ).toBeInViewport();
      await composer
        .locator(".cnooc-knowledge-scope-chip button")
        .last()
        .click();
      await expect(composer.locator(".cnooc-knowledge-scope-chip")).toHaveCount(
        18,
      );
      await page.screenshot({ path: info.outputPath("knowledge-row.png") });

      // Mixed uploads and knowledge selection share one viewport, not two rails.
      const picker = composer.locator('input[type="file"]');
      for (const count of [1, 10]) {
        const n = await composer.locator(".cnooc-attachment-card").count();
        await picker.setInputFiles(
          Array.from({ length: count - n }, (_, i) => ({
            name: `合成上传-${n + i}-长文件名布局验证.docx`,
            mimeType:
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            buffer: Buffer.from("Synthetic file; never submitted."),
          })),
        );
        await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(
          count,
        );
        expect((await header.boundingBox())!.height).toBeLessThanOrEqual(56);
        await ordered(header, composer.locator(".cnooc-composer-input"));
        await expect(input).toHaveValue(draft);
      }
      const mixedHeight = (await composer.boundingBox())!.height;
      while (await composer.locator(".cnooc-knowledge-scope-chip").count()) {
        const remaining = await composer
          .locator(".cnooc-knowledge-scope-chip")
          .count();
        await composer
          .locator(".cnooc-knowledge-scope-chip button")
          .last()
          .click();
        await expect(
          composer.locator(".cnooc-knowledge-scope-chip"),
        ).toHaveCount(remaining - 1);
      }
      expect((await composer.boundingBox())!.height).toBeLessThanOrEqual(
        mixedHeight + 1,
      );
      expect(
        await header.evaluate((el) => el.scrollHeight > el.clientHeight),
      ).toBe(true);
      await header.evaluate((el) => {
        el.scrollTop = el.scrollHeight;
      });
      await expect(
        composer.locator(".cnooc-attachment-card").last(),
      ).toBeInViewport();
      await composer.locator(".cnooc-attachment-card button").last().click();
      await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(9);
      await ordered(header, composer.locator(".cnooc-composer-input"));
      const box = (await input.boundingBox())!;
      // A real pointer click at the first text line must hit the textarea.
      expect(
        await page.evaluate(
          ({ x, y }) => document.elementFromPoint(x, y)?.tagName,
          { x: box.x + 12, y: box.y + 12 },
        ),
      ).toBe("TEXTAREA");
      await expect
        .poll(() => page.evaluate(() => document.documentElement.scrollWidth))
        .toBeLessThanOrEqual(viewport.width);
      await page.screenshot({ path: info.outputPath("uploads-row.png") });
      for (let remaining = 9; remaining > 0; remaining--) {
        await composer.locator(".cnooc-attachment-card button").last().click();
        await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(
          remaining - 1,
        );
      }
      await expect(input).toHaveValue(draft);
      await expect(composer.locator(".cnooc-submit")).toBeEnabled();
      expect(errors).toEqual([]);
    });
  }
}
