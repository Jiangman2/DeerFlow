import { expect, test, type Locator, type Page } from "@playwright/test";

import {
  mockLangGraphAPI,
  MOCK_THREAD_ID,
  MOCK_SIDECAR_THREAD_ID,
} from "./utils/mock-api";

async function openRestoredSidecar(page: Page) {
  mockLangGraphAPI(page, {
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "Composer layout check",
        messages: [
          {
            type: "human",
            id: "main-question",
            content: "Compare the two options.",
          },
          {
            type: "ai",
            id: "main-answer",
            content: "Main conversation remains available.",
          },
        ],
      },
      {
        thread_id: MOCK_SIDECAR_THREAD_ID,
        title: "Side conversation",
        metadata: {
          platform_sidecar: true,
          parent_thread_id: MOCK_THREAD_ID,
        },
        messages: [
          {
            type: "human",
            id: "side-question",
            content: "Explain the second option.",
          },
          {
            type: "ai",
            id: "side-answer",
            content: "Side conversation keeps its own history.",
          },
        ],
      },
    ],
  });
  await page.route("**/api/threads/*/runs/*/events?**", (route) =>
    route.fulfill({ json: { events: [], has_more: false } }),
  );
  await page.route("**/api/threads/*/runs/*/workspace-changes?**", (route) =>
    route.fulfill({ json: { files: [], status: "complete" } }),
  );
  await page.goto(`/workspace/chats/${MOCK_THREAD_ID}`);
  if (page.viewportSize()!.width < 768) {
    await page.getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("menuitem", { name: /open side chat/i }).click();
  } else {
    await expect(page.getByTestId("sidecar-header-trigger")).toBeVisible({
      timeout: 60_000,
    });
    await page.getByTestId("sidecar-header-trigger").click();
  }
  await expect(page.getByTestId("sidecar-panel")).toBeVisible();
  await expect
    .poll(async () =>
      page
        .getByTestId("sidecar-panel")
        .evaluate(
          (element) =>
            element.getBoundingClientRect().right <= window.innerWidth + 1,
        ),
    )
    .toBe(true);
  await expect(
    page.getByText("Side conversation keeps its own history."),
  ).toBeVisible();
}

async function expectLeftAligned(input: Locator) {
  const box = await input.evaluate((element) => {
    const field = element as HTMLTextAreaElement;
    const form = field.closest("form")!;
    const rect = field.getBoundingClientRect();
    const outer = form.getBoundingClientRect();
    const style = getComputedStyle(field);
    return {
      left: rect.left - outer.left,
      right: outer.right - rect.right,
      width: rect.width,
      formWidth: outer.width,
      align: style.textAlign,
      overflow: field.scrollWidth > field.clientWidth + 1,
    };
  });
  expect(box.left).toBeGreaterThanOrEqual(8);
  expect(box.left).toBeLessThanOrEqual(20);
  expect(Math.abs(box.left - box.right)).toBeLessThanOrEqual(2);
  expect(box.width).toBeGreaterThan(box.formWidth * 0.8);
  expect(box.align).toBe("left");
  expect(box.overflow).toBe(false);
}

async function tooltipSnapshot(page: Page, trigger: Locator) {
  await trigger.hover();
  const tooltip = page
    .locator('[data-slot="tooltip-content"]')
    .filter({ visible: true });
  await expect(tooltip).toBeVisible();
  const snapshot = await tooltip.evaluate((element) => {
    const style = getComputedStyle(element);
    // Radix adds a hidden accessibility copy inside the content.
    const text = Array.from(element.childNodes)
      .filter((n) => n.nodeType === Node.TEXT_NODE)
      .map((n) => n.textContent)
      .join("");
    return {
      text,
      color: style.color,
      background: style.backgroundColor,
      radius: style.borderRadius,
      fontSize: style.fontSize,
      padding: style.padding,
    };
  });
  await page.mouse.move(1, 1);
  await page.keyboard.press("Escape");
  await expect(tooltip).toHaveCount(0);
  return snapshot;
}

for (const viewport of [
  { width: 1440, height: 960 },
  { width: 390, height: 844 },
]) {
  test(`side chat composer is aligned and opaque at ${viewport.width}px`, async ({
    page,
  }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize(viewport);
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await openRestoredSidecar(page);
    const panel = page.getByTestId("sidecar-panel");
    const input = panel.locator("textarea[name=message]");
    await expectLeftAligned(input);
    const surface = await panel.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        background: style.backgroundColor,
        image: style.backgroundImage,
      };
    });
    expect(surface.image).toBe("none");
    expect(surface.background).not.toMatch(
      /transparent|rgba\([^)]*,\s*0(?:\.\d+)?\)/,
    );
    const text = Array.from(
      { length: 18 },
      (_, i) => `Line ${i + 1}: multi-line input stays readable.`,
    ).join("\n");
    await input.fill(text);
    await expectLeftAligned(input);
    const scrolling = await input.evaluate((element) => {
      const field = element as HTMLTextAreaElement;
      field.scrollTop = field.scrollHeight;
      return {
        height: field.clientHeight,
        total: field.scrollHeight,
        top: field.scrollTop,
      };
    });
    expect(scrolling.height).toBeGreaterThan(40);
    expect(scrolling.total).toBeGreaterThan(scrolling.height);
    expect(scrolling.top).toBeGreaterThan(0);
    await panel.locator('input[type="file"]').setInputFiles({
      name: "layout-check.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Synthetic attachment"),
    });
    await expect(panel.getByText("layout-check.txt")).toBeVisible();
    await expectLeftAligned(input);
    await expect(input).toHaveValue(text);
    const form = panel.locator("form");
    const attachment = panel.getByText("layout-check.txt");
    const formBox = await form.boundingBox();
    const attachmentBox = await attachment.boundingBox();
    expect(attachmentBox!.x - formBox!.x).toBeGreaterThanOrEqual(8);
    expect(attachmentBox!.y - formBox!.y).toBeGreaterThanOrEqual(8);
    await page.screenshot({
      path: testInfo.outputPath(`sidechat-${viewport.width}.png`),
      fullPage: true,
      animations: "disabled",
    });
    expect(pageErrors).toEqual([]);
  });
}

test("side chat attachment and mode hints match the main composer", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 960 });
  await openRestoredSidecar(page);
  const main = page
    .locator("form")
    .filter({ has: page.getByPlaceholder(/how can i assist you/i) });
  const side = page.getByTestId("sidecar-panel");
  const mainAttachment = await tooltipSnapshot(
    page,
    main.getByTestId("add-attachments-button"),
  );
  const sideAttachment = await tooltipSnapshot(
    page,
    side.getByTestId("sidecar-add-attachments-button"),
  );
  expect(sideAttachment).toEqual(mainAttachment);
  expect(sideAttachment.text).toBe("Add attachments");
  const mainMode = await tooltipSnapshot(
    page,
    main.getByRole("button", { name: "Flash", exact: true }),
  );
  const sideMode = await tooltipSnapshot(
    page,
    side.getByRole("button", { name: "Flash", exact: true }),
  );
  expect(sideMode).toEqual(mainMode);
  expect(sideMode.text).toContain("Flash:");
  await side.getByRole("button", { name: "Flash", exact: true }).click();
  await expect(page.getByRole("menuitem", { name: /^Pro / })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(side.locator("textarea[name=message]")).toHaveValue("");
});
