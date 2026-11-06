import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

import {
  MOCK_SIDECAR_THREAD_ID,
  MOCK_THREAD_ID,
  mockLangGraphAPI,
} from "./utils/mock-api";

test.setTimeout(120_000);

// Chromium headless hides scrollbars by default; keep their real layout width.
test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });

const employee = {
  name: "autosize-agent",
  display_name: "输入框测试助手",
  description: "合成历史对话，仅验证输入布局。",
  system_prompt: "Synthetic layout test only.",
};
const lines = (count: number) =>
  Array.from({ length: count }, (_, index) => `第${index + 1}行内容`).join(
    "\n",
  );
const attachment = {
  name: "自动高度测试.txt",
  mimeType: "text/plain",
  buffer: Buffer.from("Synthetic local attachment. Never submitted."),
};

async function openHistory(
  page: Page,
  baseURL: string,
  options: { employee?: boolean; fixedSizing?: boolean } = {},
) {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  const consoleWarnings: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
    if (message.type() === "warning") consoleWarnings.push(message.text());
  });
  await page
    .context()
    .addCookies([{ name: "locale", value: "zh-CN", url: baseURL }]);
  mockLangGraphAPI(page, {
    agents: [employee],
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "历史输入框自动高度测试",
        ...(options.employee ? { agent_name: employee.name } : {}),
        messages: [
          { type: "human", id: "old-question", content: "合成历史问题" },
          { type: "ai", id: "old-answer", content: "合成历史回复" },
        ],
      },
      {
        thread_id: MOCK_SIDECAR_THREAD_ID,
        title: "合成侧边对话",
        metadata: { platform_sidecar: true, parent_thread_id: MOCK_THREAD_ID },
        messages: [
          { type: "human", id: "side-question", content: "合成侧边问题" },
          { type: "ai", id: "side-answer", content: "合成侧边回复" },
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
  const url = options.employee
    ? `/workspace/agents/${employee.name}/chats/${MOCK_THREAD_ID}`
    : `/workspace/chats/${MOCK_THREAD_ID}`;
  await page.goto(url, { waitUntil: "domcontentloaded" });
  if (options.fixedSizing !== false) {
    // Exercise the actual fallback used by enterprise browsers without field-sizing.
    await page.addStyleTag({
      content: "textarea { field-sizing: fixed !important; }",
    });
  }
  const composer = page.getByTestId("chat-composer");
  const input = composer.locator("textarea[name=message]");
  await expect(input).toBeEditable({ timeout: 90_000 });
  await expect(page.getByText("合成历史回复", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${url}$`));
  expect(await page.title()).not.toBe("");
  return {
    composer,
    input,
    health: async (info: TestInfo) => {
      expect(pageErrors).toEqual([]);
      expect(consoleErrors).toEqual([]);
      await expect(
        page.locator("nextjs-portal [data-nextjs-dialog-overlay]"),
      ).not.toBeVisible();
      await info.attach("browser-health", {
        body: JSON.stringify({
          url: page.url(),
          title: await page.title(),
          pageErrors,
          consoleErrors,
          consoleWarnings,
        }),
        contentType: "application/json",
      });
    },
  };
}

async function dimensions(input: Locator) {
  return input.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      height: element.clientHeight,
      scroll: element.scrollHeight,
      width: element.clientWidth,
      scrollWidth: element.scrollWidth,
      line: parseFloat(style.lineHeight),
      top: rect.top,
      bottom: rect.bottom,
    };
  });
}

async function expectLines(input: Locator, count: number) {
  await expect
    .poll(async () => {
      const value = await dimensions(input);
      return Math.abs(value.height - value.line * count);
    })
    .toBeLessThanOrEqual(1);
}

async function expectInternalScroll(input: Locator) {
  const value = await dimensions(input);
  expect(value.scroll).toBeGreaterThan(value.height + 1);
  expect(value.scrollWidth).toBeLessThanOrEqual(value.width + 1);
  await input.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  expect(await input.evaluate((element) => element.scrollTop)).toBeGreaterThan(
    0,
  );
  await input.evaluate((element) => {
    element.scrollTop = 0;
  });
}

const viewports = [
  { width: 1440, height: 960 },
  { width: 320, height: 740 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 844, height: 390 },
];

for (const viewport of viewports) {
  test(`history draft grows upwards to six lines and shrinks ${viewport.width}x${viewport.height}`, async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize(viewport);
    const { composer, input, health } = await openHistory(page, baseURL!);
    const form = composer.locator("form.cnooc-composer");
    await input.fill(lines(1));
    await expectLines(input, 1);
    const shortBox = (await form.boundingBox())!;
    if (viewport.width === 1440)
      await page.screenshot({ path: info.outputPath("one-line.png") });
    await input.fill(lines(3));
    await expectLines(input, 3);
    await input.fill(lines(6));
    await expectLines(input, 6);
    await expect
      .poll(async () =>
        Math.abs(
          (await form.boundingBox())!.y +
            (await form.boundingBox())!.height -
            shortBox.y -
            shortBox.height,
        ),
      )
      .toBeLessThanOrEqual(1);
    const sixBox = (await form.boundingBox())!;
    expect(sixBox.y).toBeLessThan(shortBox.y - 40);
    expect(sixBox.y + sixBox.height).toBeLessThanOrEqual(viewport.height + 1);
    if ([1440, 390, 844].includes(viewport.width))
      await page.screenshot({ path: info.outputPath("six-lines.png") });
    await input.fill(lines(10));
    await expectLines(input, 6);
    await expectInternalScroll(input);
    expect((await form.boundingBox())!.height).toBeCloseTo(sixBox.height, 0);

    await composer.locator('input[type="file"]').setInputFiles(attachment);
    await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(1);
    await expect(input).toHaveValue(lines(10));
    await expectLines(input, 6);
    const rail = (await composer
      .locator(".cnooc-composer-header")
      .boundingBox())!;
    expect((await input.boundingBox())!.y).toBeGreaterThanOrEqual(
      rail.y + rail.height - 1,
    );
    if (viewport.width === 390)
      await page.screenshot({
        path: info.outputPath("six-lines-with-attachment.png"),
      });
    await input.fill(lines(1));
    await expectLines(input, 1);
    await composer.locator(".cnooc-attachment-card button").click();
    await expect(composer.locator(".cnooc-attachment-card")).toHaveCount(0);
    await expect(input).toHaveValue(lines(1));
    await expectLines(input, 1);
    await expect
      .poll(async () =>
        Math.abs((await form.boundingBox())!.height - shortBox.height),
      )
      .toBeLessThanOrEqual(1);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(viewport.width);
    await health(info);
  });
}

test("soft-wrapped paste remeasures when the viewport narrows", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const { input, health } = await openHistory(page, baseURL!);
  const text = "连续中文输入应该自动换行并随可用宽度调整高度。".repeat(4);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.evaluate((value) => navigator.clipboard.writeText(value), text);
  await input.focus();
  await input.press("ControlOrMeta+V");
  await expect(input).toHaveValue(text);
  await expect
    .poll(async () => (await dimensions(input)).height)
    .toBeGreaterThan(24);
  const wide = await dimensions(input);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(async () => (await dimensions(input)).width)
    .toBeLessThan(wide.width);
  await expect
    .poll(async () => (await dimensions(input)).height)
    .toBeGreaterThan(wide.height);
  expect((await dimensions(input)).height).toBeLessThanOrEqual(144);
  await expect(input).toHaveValue(text);
  await input.fill("LongUnbrokenToken".repeat(100));
  await expectLines(input, 6);
  await expectInternalScroll(input);
  await page.screenshot({ path: info.outputPath("narrowed-main-draft.png") });
  await health(info);
});

test("native field-sizing browser also honors six lines", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const { input, health } = await openHistory(page, baseURL!, {
    fixedSizing: false,
  });
  await input.fill(lines(3));
  await expectLines(input, 3);
  await input.fill(lines(9));
  await expectLines(input, 6);
  await expectInternalScroll(input);
  await input.fill("");
  await expectLines(input, 1);
  await health(info);
});

test("employee history uses the same growing draft", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { input, health } = await openHistory(page, baseURL!, {
    employee: true,
  });
  await input.fill(lines(4));
  await expectLines(input, 4);
  await input.fill(lines(9));
  await expectLines(input, 6);
  await expectInternalScroll(input);
  await input.fill(lines(1));
  await expectLines(input, 1);
  await health(info);
});

test("slash draft grows with attachments and restores the textarea", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { composer, input, health } = await openHistory(page, baseURL!);
  await composer.locator('input[type="file"]').setInputFiles(attachment);
  await input.fill("/dat");
  await page.getByRole("option", { name: /data-analysis/i }).click();
  const editor = composer.locator('[contenteditable="true"]');
  await expect(editor).toBeVisible();
  const viewport = editor.locator("..");
  await editor.fill(lines(9));
  await expectLines(viewport, 6);
  await expectInternalScroll(viewport);
  await editor.fill(lines(1));
  await expectLines(viewport, 1);
  await editor.fill(lines(5));
  const slashDraft = await editor.innerText();
  expect(slashDraft.trimEnd()).toBe(lines(5));
  await composer
    .getByRole("button", { name: "Remove /data-analysis", exact: true })
    .click();
  await expect(input).toHaveValue(slashDraft);
  await expectLines(input, Math.min(6, slashDraft.split("\n").length));
  await input.fill(lines(1));
  await expectLines(input, 1);
  await health(info);
});

test("IME and Shift Enter preserve the draft; Enter submits and collapses", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 1440, height: 960 });
  const { input, health } = await openHistory(page, baseURL!);
  let streamCalls = 0;
  let submitted: unknown;
  await page.route("**/api/langgraph/threads/*/runs/stream", async (route) => {
    streamCalls += 1;
    submitted = route.request().postDataJSON();
    await route.fallback();
  });
  await input.fill("输入法候选");
  await input.dispatchEvent("compositionstart", { data: "候选" });
  await input.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    isComposing: true,
    keyCode: 229,
  });
  expect(streamCalls).toBe(0);
  await expect(input).toHaveValue("输入法候选");
  await input.dispatchEvent("compositionend", { data: "候选" });
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("输入法候选\n");
  await expectLines(input, 2);
  expect(streamCalls).toBe(0);
  const draft = lines(4);
  await input.fill(draft);
  await expectLines(input, 4);
  await input.press("Enter");
  await expect.poll(() => streamCalls).toBe(1);
  expect(JSON.stringify(submitted)).toContain(
    JSON.stringify(draft).slice(1, -1),
  );
  await expect(
    page.getByText("Hello from DeerFlow!", { exact: false }).first(),
  ).toBeVisible();
  await expect(input).toHaveValue("");
  await expectLines(input, 1);
  await health(info);
});

test("typing past six lines keeps the caret visible at the end", async ({
  page,
  baseURL,
}, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { input, health } = await openHistory(page, baseURL!);
  await input.focus();
  for (let index = 1; index <= 9; index += 1) {
    if (index > 1) await input.press("Shift+Enter");
    await page.keyboard.insertText(`第${index}行连续输入`);
  }
  await expectLines(input, 6);
  await expect
    .poll(() =>
      input.evaluate((element: HTMLTextAreaElement) => {
        return element.scrollHeight - element.clientHeight - element.scrollTop;
      }),
    )
    .toBeLessThanOrEqual(2);
  const caret = await input.evaluate((element: HTMLTextAreaElement) => ({
    start: element.selectionStart,
    end: element.selectionEnd,
    length: element.value.length,
  }));
  expect(caret.start).toBe(caret.length);
  expect(caret.end).toBe(caret.length);
  await page.screenshot({ path: info.outputPath("typing-at-scroll-end.png") });
  await health(info);
});

test.describe("classic scrollbar wrapping", () => {
  test("soft-wrapped lines use the full width until the six-line cap", async ({
    page,
    baseURL,
  }, info) => {
    await page.setViewportSize({ width: 1440, height: 960 });
    const { input, health } = await openHistory(page, baseURL!);
    await page.addStyleTag({
      content: `
      [data-testid="chat-composer"] textarea[name="message"] {
        box-sizing: border-box !important;
        width: 300px !important;
        min-width: 300px !important;
        max-width: 300px !important;
        flex: none !important;
        padding: 0 !important;
        border: 0 !important;
        font: 13px/24px monospace !important;
        letter-spacing: 0 !important;
        scrollbar-width: auto !important;
        scrollbar-gutter: auto !important;
      }
    `,
    });

    async function naturalTextHeight(value: string) {
      return input.evaluate((element: HTMLTextAreaElement, draft) => {
        // An independent browser text layout supplies the expected wrap height.
        // Copy the outer width and box model, without reserving a scrollbar gutter.
        const computed = getComputedStyle(element);
        const mirror = document.createElement("textarea");
        mirror.value = draft;
        mirror.rows = 1;
        Object.assign(mirror.style, {
          position: "fixed",
          left: "-10000px",
          top: "0",
          visibility: "hidden",
          width: `${element.getBoundingClientRect().width}px`,
          height: "0px",
          minHeight: "0px",
          maxHeight: "none",
          overflow: "hidden",
          resize: "none",
          boxSizing: computed.boxSizing,
          font: computed.font,
          lineHeight: computed.lineHeight,
          letterSpacing: computed.letterSpacing,
          paddingTop: computed.paddingTop,
          paddingRight: computed.paddingRight,
          paddingBottom: computed.paddingBottom,
          paddingLeft: computed.paddingLeft,
          borderTopWidth: computed.borderTopWidth,
          borderRightWidth: computed.borderRightWidth,
          borderBottomWidth: computed.borderBottomWidth,
          borderLeftWidth: computed.borderLeftWidth,
          borderStyle: computed.borderStyle,
        });
        mirror.style.setProperty("field-sizing", "fixed");
        document.body.append(mirror);
        const height = mirror.scrollHeight;
        mirror.remove();
        return height;
      }, value);
    }

    const shortDraft = "x".repeat(74);
    for (const [draft, expectedLines] of [
      [shortDraft, 2],
      ["x".repeat(222), 6],
    ] as const) {
      const naturalHeight = await naturalTextHeight(draft);
      expect(naturalHeight).toBeCloseTo(
        (await dimensions(input)).line * expectedLines,
        0,
      );
      await input.fill(draft);
      await expect
        .poll(async () => (await dimensions(input)).height)
        .toBe(naturalHeight);
      const rendered = await dimensions(input);
      expect(rendered.scroll).toBeLessThanOrEqual(rendered.height + 1);
    }

    await input.fill("x".repeat(400));
    await expectLines(input, 6);
    await expectInternalScroll(input);
    // Prove that this browser really reserves horizontal space for its scrollbar.
    expect(
      await input.evaluate(
        (element: HTMLTextAreaElement) =>
          element.offsetWidth - element.clientWidth,
      ),
    ).toBeGreaterThan(0);
    await page.screenshot({
      path: info.outputPath("classic-scrollbar-overflow.png"),
    });

    const shortHeight = await naturalTextHeight(shortDraft);
    await input.fill(shortDraft);
    await expect
      .poll(async () => (await dimensions(input)).height)
      .toBe(shortHeight);
    expect((await dimensions(input)).scroll).toBeLessThanOrEqual(
      shortHeight + 1,
    );
    await health(info);
  });
});
