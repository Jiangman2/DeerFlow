import { expect, test, type Locator } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const agent = {
  name: "attachment-layout-agent",
  display_name: "附件布局验收助手",
  description:
    "用于验证数字员工简介、上传附件和输入文字的布局。这是一段较长的合成简介，附件换行后仍应完整位于输入框上方。".repeat(3),
  system_prompt: "Synthetic layout test only.",
};
const draft = "请分析这些附件，输入的文字不应被附件遮挡。";

async function expectOrdered(above: Locator, below: Locator) {
  await expect.poll(async () => {
    const a = await above.boundingBox();
    const b = await below.boundingBox();
    return a && b ? b.y - (a.y + a.height) : -1;
  }).toBeGreaterThanOrEqual(0);
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
  { width: 390, height: 844 },
  { width: 320, height: 740 },
  { width: 844, height: 390 },
]) {
  test(`agent attachments stay separate from draft and introduction ${viewport.width}x${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport);
    mockLangGraphAPI(page, { agents: [agent] });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`/workspace/agents/${agent.name}/chats/new`);
    const composer = page.getByTestId("welcome-composer");
    const input = composer.locator("textarea");
    const header = composer.locator(".cnooc-composer-header");
    const welcome = page.locator(".cnooc-agent-welcome-stage");
    await expect(input).toBeEditable({ timeout: 20_000 });
    await input.fill(draft);
    const picker = composer.locator('input[type="file"]');

    let singleAttachmentHeight = 0;
    for (const count of [1, 5, 10]) {
      const existing = await composer.locator(".cnooc-attachment-card").count();
      await picker.setInputFiles(Array.from({ length: count - existing }, (_, n) => ({
        name: `合成附件-${existing + n + 1}-长文件名布局验证.docx`,
        mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        buffer: Buffer.from("Synthetic attachment; never submitted to a backend."),
      })));
      await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(count);
      if (count === 1) singleAttachmentHeight = (await composer.boundingBox())!.height;
      await expect.poll(async () => (await composer.boundingBox())!.height).toBeLessThanOrEqual(singleAttachmentHeight + 4);
      await expect(input).toHaveValue(draft);
      if (await welcome.isVisible()) await expectOrdered(welcome, composer);
      await expectOrdered(header, composer.locator(".cnooc-composer-input"));
      await expectOrdered(input, composer.locator(".cnooc-submit"));
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(viewport.width);
      if (count === 10) {
        expect((await header.boundingBox())!.height).toBeLessThanOrEqual(viewport.width < 768 ? 80 : 56);
        if (viewport.width >= 768) {
          expect(await header.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
          await header.evaluate((el) => { el.scrollTop = el.scrollHeight; });
          await expect(composer.locator(".cnooc-attachment-card").last()).toBeInViewport();
          await header.evaluate((el) => { el.scrollTop = 0; });
        }
        await page.screenshot({ path: testInfo.outputPath("ten-attachments.png"), fullPage: false });
      }
    }

    // Deletion must remain usable even when the last card starts outside the rail.
    await composer.locator(".cnooc-attachment-card button").last().click();
    await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(9);
    while (await composer.locator(".cnooc-attachment-card").count()) {
      await composer.locator(".cnooc-attachment-card button").first().click();
    }
    await expect(input).toHaveValue(draft);
    await input.fill(Array(12).fill(draft).join("\n"));
    await expectOrdered(composer.locator(".cnooc-composer-input"), composer.locator(".cnooc-submit"));
    await expect(composer.locator(".cnooc-submit")).toBeEnabled();
    expect(errors).toEqual([]);
  });
}
