import { expect, test } from "@playwright/test";

import {
  PORTAL_MORE_TOOLS,
  PORTAL_QUICK_TOOLS,
} from "../../src/core/portal-links";
import { preparePublicPptRequest } from "../../src/core/skills/public-ppt-request";

import { handleRunStream, mockLangGraphAPI } from "./utils/mock-api";

test.setTimeout(90_000);
test.beforeEach(async ({ page }, info) => {
  mockLangGraphAPI(page, {
    skills: [
      {
        name: "ppt-master",
        category: "public",
        enabled: true,
        description: "PPT Skill",
      },
    ],
  });
  await page.route("**/api/announcements/active", (r) =>
    r.fulfill({ json: { announcement: null } }),
  );
  await page.route("**/integrations/haineng/model-call-count", (r) =>
    r.fulfill({ json: { todayModelCallCount: 0, totalModelCallCount: 0 } }),
  );
  await page.route("**/api/home/statistics", (r) =>
    r.fulfill({ json: { todayUsers: 0, todayTasks: 0 } }),
  );
  await page.route("**/api/features", (r) =>
    r.fulfill({
      json: {
        agents_api: { enabled: true },
        browser_control: { enabled: false },
        ppt_confirmation: { enabled: true, protocol: "ppt-confirmation-v1" },
      },
    }),
  );
  await page
    .context()
    .addCookies([
      { name: "locale", value: "zh-CN", url: String(info.project.use.baseURL) },
    ]);
});

for (const { width, height } of [
  { width: 1440, height: 900 },
  { width: 320, height: 720 },
  { width: 375, height: 812 },
  { width: 390, height: 844 },
  { width: 412, height: 915 },
  { width: 844, height: 390 },
]) {
  test(`navigation icons survive unavailable PNGs and cold load at ${width}x${height}`, async ({
    page,
  }, info) => {
    const errors: string[] = [];
    const imageRequests: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.route(
      /(?:\/images\/portal[^?]*|\/_next\/image\?.*portal)/,
      (r) => {
        imageRequests.push(r.request().url());
        return r.abort();
      },
    );
    await page.setViewportSize({ width, height });
    await page.goto("/workspace/chats/new");
    await expect(page.locator('textarea[name="message"]')).toBeEditable();
    for (const t of PORTAL_QUICK_TOOLS) {
      const link = page.getByTestId(`portal-tool-${t.id}`);
      await link.scrollIntoViewIfNeeded();
      await expect(link.getByTestId(`portal-icon-${t.id}`)).toBeVisible();
      await expect(link.locator("img")).toHaveCount(1);
      await expect(link.locator("svg")).toHaveCount(0);
      await expect(link.locator("img")).toHaveAttribute(
        "src",
        /^data:image\/webp;base64,/,
      );
      await expect
        .poll(() =>
          link
            .locator("img")
            .evaluate(
              (img: HTMLImageElement) =>
                img.complete && img.naturalWidth === 144,
            ),
        )
        .toBe(true);
      await expect(
        link.getByTestId("external-navigation-marker"),
      ).toBeVisible();
      await expect(link).toHaveAttribute("target", "_blank");
    }
    await page.getByTestId("portal-tools-trigger").click();
    for (const t of PORTAL_MORE_TOOLS) {
      const link = page.getByTestId(`portal-tool-${t.id}`);
      await expect(link.getByTestId(`portal-icon-${t.id}`)).toBeVisible();
      await expect(link.locator("img")).toHaveCount(1);
      await expect(link.locator("svg")).toHaveCount(0);
      await expect(link.locator("img")).toHaveAttribute(
        "src",
        /^data:image\/webp;base64,/,
      );
      await expect
        .poll(() =>
          link
            .locator("img")
            .evaluate(
              (img: HTMLImageElement) =>
                img.complete && img.naturalWidth === 144,
            ),
        )
        .toBe(true);
    }
    expect(imageRequests).toEqual([]);
    expect(errors).toEqual([]);
    await expect(page).toHaveURL(/\/workspace\/chats\/new$/);
    await expect(page).not.toHaveTitle("");
    await page.screenshot({
      path: info.outputPath(`generated-icons-${width}.png`),
    });
  });

  test(`PPT submits to the ordinary Agent without Runner choices at ${width}x${height}`, async ({
    page,
  }, info) => {
    const runs: { url: string; body: unknown }[] = [];
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await page.route(/\/runs\/stream$/, (r) => {
      runs.push({ url: r.request().url(), body: r.request().postDataJSON() });
      // Use the stateful mock: it persists the actual submitted message for
      // navigation, history pagination and refresh, as the server would.
      return r.fallback();
    });
    await page.setViewportSize({ width, height });
    await page.goto("/workspace/chats/new");
    const input = page.locator('textarea[name="message"]');
    await input.fill("/ppt-master 制作一份年度汇报演示文稿");
    await expect(page.getByTestId("ppt-confirmation-opt-in")).toHaveCount(0);
    await expect(page.getByTestId("ppt-submission-choice")).toHaveCount(0);
    await input.press("Enter");
    await expect.poll(() => runs.length).toBe(1);
    expect(runs[0]?.url).toContain("/api/langgraph/threads/");
    expect(runs[0]?.body).toMatchObject({
      assistant_id: "lead_agent",
      input: {
        messages: [
          expect.objectContaining({
            content: expect.arrayContaining([
              expect.objectContaining({
                text: preparePublicPptRequest({
                  input: {
                    messages: [
                      {
                        type: "human",
                        content: "/ppt-master 制作一份年度汇报演示文稿",
                      },
                    ],
                  },
                }).input.messages[0]!.content,
              }),
            ]),
          }),
        ],
      },
    });
    const payload = JSON.stringify(runs[0]?.body);
    expect(payload).not.toContain("ppt_confirmation_mode");
    expect(payload).not.toContain("ppt-master-runner");
    expect(payload).not.toContain("skill_workflow");
    await expect(
      page.getByText("制作一份年度汇报演示文稿", { exact: false }).last(),
    ).toBeVisible();
    await expect(
      page.getByText("请使用已安装且启用的 public ppt-master Skill", {
        exact: false,
      }),
    ).toHaveCount(0);
    await expect(page.getByTestId("ppt-submission-choice")).toHaveCount(0);
    await expect(page.getByTestId("ppt-confirmation-opt-in")).toHaveCount(0);
    const threadId = /\/threads\/([^/]+)\/runs\/stream$/.exec(runs[0]!.url)?.[1];
    expect(threadId).toBeTruthy();
    await page.goto(`/workspace/chats/${threadId}`);
    await page.reload();
    await expect(
      page.getByText("制作一份年度汇报演示文稿", { exact: false }).last(),
    ).toBeVisible();
    await expect(
      page.getByText("请使用已安装且启用的 public ppt-master Skill", {
        exact: false,
      }),
    ).toHaveCount(0);
    await page.screenshot({
      path: info.outputPath(`ppt-public-skill-${width}.png`),
    });
    expect(errors).toEqual([]);
  });
}

test("PPT shortcut preserves an existing workflow and requests a new chat", async ({
  page,
}) => {
  let posted = 0;
  await page.route("**/api/langgraph/threads/*/state", (r) =>
    r.fulfill({
      json: {
        values: {
          messages: [],
          skill_workflow: {
            skill_name: "ppt-master",
            status: "blocked",
            gate_status: "route-selection-required",
          },
        },
        next: [],
        metadata: {},
      },
    }),
  );
  await page.route(/\/runs\/stream$/, (r) => {
    posted++;
    return handleRunStream(r);
  });
  await page.goto("/workspace/chats/new");
  const input = page.locator('textarea[name="message"]');
  await input.fill("/ppt-master 继续制作演示文稿");
  await input.press("Enter");
  await expect(
    page
      .getByText(
        "此对话保留了旧的 Skill 工作流检查点。请新建对话后使用 /ppt-master；原对话和文件会保留。",
        { exact: true },
      )
      .first(),
  ).toBeVisible();
  expect(posted).toBe(0);
});

test("public Skill submission does not depend on Runner confirmation availability", async ({
  page,
}) => {
  await page.route("**/api/features", (r) =>
    r.fulfill({
      json: {
        agents_api: { enabled: true },
        browser_control: { enabled: false },
        ppt_confirmation: { enabled: false, protocol: "ppt-confirmation-v1" },
      },
    }),
  );
  await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
  const input = page.locator('textarea[name="message"]');
  await input.fill("/ppt-master 人工确认测试");
  const run = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/runs/stream"),
  );
  await input.press("Enter");
  expect((await run).url()).toContain("/api/langgraph/threads/");
  await expect(page.getByTestId("ppt-submission-choice")).toHaveCount(0);
});

test("public PPT Skill keeps template originals and sends ordinary Skill instructions", async ({
  page,
}) => {
  const uploads: string[] = [];
  await page.route("**/api/threads/*/uploads", async (r) => {
    if (r.request().method() !== "POST") return r.fallback();
    uploads.push(r.request().postData() ?? "");
    return r.fulfill({
      json: {
        files: [
          {
            filename: "template.pptx",
            size: 15,
            path: "uploads/template.pptx",
            artifact_url: "",
          },
        ],
      },
    });
  });
  await page.goto("/workspace/chats/new", { waitUntil: "domcontentloaded" });
  const input = page.locator('textarea[name="message"]');
  await input.fill("/ppt-master 使用这份模板");
  const composer = page.getByTestId("welcome-composer");
  await composer.locator('input[type="file"]').setInputFiles({
    name: "template.pptx",
    mimeType:
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    buffer: Buffer.from("offline fixture"),
  });
  await expect(
    composer.getByText("template.pptx", { exact: true }),
  ).toBeVisible();
  const run = page.waitForRequest(
    (r) => r.method() === "POST" && r.url().endsWith("/runs/stream"),
  );
  await input.press("Enter");
  const request = await run;
  expect(request.url()).toContain("/api/langgraph/threads/");
  expect(JSON.stringify(request.postDataJSON())).toContain(
    "/mnt/skills/public/ppt-master/SKILL.md",
  );
  expect(uploads).toHaveLength(1);
  expect(uploads[0]).toContain("presentation_template");
  await expect(page.getByTestId("ppt-submission-choice")).toHaveCount(0);
});
