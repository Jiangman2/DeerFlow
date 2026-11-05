import { expect, test } from "@playwright/test";

import { MOCK_THREAD_ID, mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(120_000);
const agent = {
  name: "multiline-agent",
  display_name: "多行输入测试助手",
  description: "附件不遮挡简介，文字区域保留多行。",
  system_prompt: "Synthetic layout test.",
};
const draft = Array.from(
  { length: 15 },
  (_, i) =>
    `第${i + 1}行：这是上传附件前已输入的文字，换行和完整内容都需要保留。`,
).join("\n");

for (const width of [1440, 390]) {
  for (const employee of [false, true]) {
    for (const existing of [false, true]) {
      test(`multiline ${employee ? "employee" : "main"} ${existing ? "chat" : "welcome"} ${width}`, async ({
        page,
        baseURL,
      }, info) => {
        await page.setViewportSize({ width, height: 900 });
        await page
          .context()
          .addCookies([{ name: "locale", value: "zh-CN", url: baseURL! }]);
        mockLangGraphAPI(page, {
          agents: [agent],
          threads: [
            {
              thread_id: MOCK_THREAD_ID,
              messages: [
                { type: "human", id: "h", content: "测试" },
                { type: "ai", id: "a", content: "回复" },
              ],
            },
          ],
        });
        const prefix = employee
          ? `/workspace/agents/${agent.name}/chats/`
          : "/workspace/chats/";
        await page.goto(prefix + (existing ? MOCK_THREAD_ID : "new"), {
          waitUntil: "domcontentloaded",
        });
        // Emulate the enterprise browser's lack of content-sized textarea support.
        await page.addStyleTag({
          content: "textarea { field-sizing: fixed !important; }",
        });
        const composer = page.getByTestId(
          existing ? "chat-composer" : "welcome-composer",
        );
        const input = composer.locator("textarea");
        await expect(input).toBeEditable({ timeout: 60_000 });
        await input.fill(draft);
        await composer
          .locator('input[type="file"]')
          .setInputFiles(
            Array.from({ length: 10 }, (_, i) => ({
              name: `测试附件${i}.docx`,
              mimeType:
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              buffer: Buffer.from("Synthetic only. Never uploaded."),
            })),
          );
        await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(
          10,
        );
        await expect(input).toHaveValue(draft);
        const dimensions = await input.evaluate((el) => ({
          height: el.clientHeight,
          scroll: el.scrollHeight,
          line: parseFloat(getComputedStyle(el).lineHeight),
        }));
        expect(dimensions.height).toBeGreaterThanOrEqual(
          dimensions.line * 3 - 1,
        );
        expect(dimensions.scroll).toBeGreaterThan(dimensions.height);
        await input.evaluate((el) => {
          el.scrollTop = el.scrollHeight;
        });
        expect(await input.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
        await input.evaluate((el) => {
          el.scrollTop = 0;
        });
        const header = composer.locator(".cnooc-composer-header");
        expect((await header.boundingBox())!.height).toBeLessThanOrEqual(49);
        const a = (await header.boundingBox())!,
          b = (await input.boundingBox())!,
          c = (await composer.locator(".cnooc-submit").boundingBox())!;
        expect(b.y).toBeGreaterThanOrEqual(a.y + a.height - 1);
        expect(b.y + b.height).toBeLessThanOrEqual(c.y + 1);
        const shellHeight = (await composer.boundingBox())!.height;
        await input.fill(draft.repeat(5));
        expect((await composer.boundingBox())!.height).toBeLessThanOrEqual(
          shellHeight + 1,
        );
        await input.fill(draft);
        await input.scrollIntoViewIfNeeded();
        await page.screenshot({
          path: info.outputPath("multiline-with-attachments.png"),
        });
        await header.evaluate((el) => {
          el.scrollTop = el.scrollHeight;
        });
        await composer.locator(".cnooc-attachment-card button").last().click();
        await expect(input).toHaveValue(draft);
        expect(
          await page.evaluate(() => document.documentElement.scrollWidth),
        ).toBeLessThanOrEqual(width);
      });
    }
  }
}
