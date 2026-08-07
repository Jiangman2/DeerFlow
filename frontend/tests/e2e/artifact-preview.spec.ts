import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const ARTIFACT_PATH = "/artifact-fixtures/report.html";
const MARKDOWN_ARTIFACT_PATH = "/artifact-fixtures/report.md";
const JSON_ARTIFACT_PATH = "/artifact-fixtures/report.json";
const PRESENTED_ARTIFACT_PATH = "/mnt/user-data/outputs/presented-report.md";
const PRESENTED_PPTX_FILENAME =
  "中国海油勘探开发智能体平台研究报告_20260805_111535.pptx";
const PRESENTED_PPTX_PATH = `/mnt/user-data/outputs/${PRESENTED_PPTX_FILENAME}`;
const PRESENTED_PPTX_PREVIEW_PATH =
  "/mnt/user-data/outputs/中国海油勘探开发智能体平台研究报告_20260805_111535.preview.pdf";
const IN_PROGRESS_THREAD_ID = "00000000-0000-0000-0000-000000003119";
const COMPLETE_THREAD_ID = "00000000-0000-0000-0000-000000003120";
const MARKDOWN_THREAD_ID = "00000000-0000-0000-0000-000000003121";
const MARKDOWN_ANCHOR_THREAD_ID = "00000000-0000-0000-0000-000000003123";
const JSON_THREAD_ID = "00000000-0000-0000-0000-000000003122";
const PRESENTED_THREAD_ID = "00000000-0000-0000-0000-000000003123";
const PRESENTED_PPTX_THREAD_ID = "00000000-0000-0000-0000-000000003124";

function writeFileMessages({
  path = ARTIFACT_PATH,
  content = "<!doctype html><html><body><h1>Report draft</h1><p>测试内容</p></body></html>",
  toolResult,
}: {
  path?: string;
  content?: string;
  toolResult?: string;
} = {}) {
  const messages: unknown[] = [
    {
      type: "human",
      id: "msg-human-artifact",
      content: [{ type: "text", text: "Create a report artifact" }],
    },
    {
      type: "ai",
      id: "msg-ai-write-artifact",
      content: "",
      tool_calls: [
        {
          id: "write-file-artifact",
          name: "write_file",
          args: {
            description: "Writing report artifact",
            path,
            content,
          },
        },
      ],
    },
  ];

  if (toolResult !== undefined) {
    messages.push({
      type: "tool",
      id: "msg-tool-write-artifact",
      name: "write_file",
      tool_call_id: "write-file-artifact",
      content: toolResult,
    });
  }

  return messages;
}

function presentFilesMessages() {
  return [
    {
      type: "human",
      id: "msg-human-present-file",
      content: [{ type: "text", text: "Create a markdown report" }],
    },
    {
      type: "ai",
      id: "msg-ai-present-file",
      content: "The report has been written. Now let me present the file.",
      tool_calls: [
        {
          id: "present-file-artifact",
          name: "present_files",
          args: {
            filepaths: [PRESENTED_ARTIFACT_PATH],
          },
        },
      ],
    },
    {
      type: "tool",
      id: "msg-tool-present-file",
      name: "present_files",
      tool_call_id: "present-file-artifact",
      status: "success",
      content: "Published",
      artifact: { presented_files: [PRESENTED_ARTIFACT_PATH] },
    },
  ];
}

test.describe("Artifact preview stability", () => {
  test.beforeEach(async ({ page }) => {
    const email = process.env.PLAYWRIGHT_AUTH_EMAIL;
    const password = process.env.PLAYWRIGHT_AUTH_PASSWORD;
    if (email === undefined || password === undefined) {
      return;
    }

    const response = await page.request.post("/api/v1/auth/login/local", {
      form: { username: email, password },
    });
    expect(response.ok()).toBeTruthy();
  });

  test("renders preview iframe for an in-progress write artifact", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: IN_PROGRESS_THREAD_ID,
          title: "Artifact preview in progress",
          messages: writeFileMessages(),
        },
      ],
    });

    await page.goto(`/workspace/chats/${IN_PROGRESS_THREAD_ID}`);

    await expect(page.getByText(ARTIFACT_PATH)).toBeVisible({
      timeout: 15_000,
    });
    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "true");
    await page.getByText(ARTIFACT_PATH).click();

    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "false");
    await expect(artifactsPanel.getByText("report.html")).toBeVisible();
    await expect(
      artifactsPanel.locator('iframe[title="Artifact preview"]'),
    ).toBeVisible();
  });

  test("renders preview iframe after the write artifact succeeds", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: COMPLETE_THREAD_ID,
          title: "Artifact preview complete",
          messages: writeFileMessages({ toolResult: "OK" }),
        },
      ],
    });

    await page.goto(`/workspace/chats/${COMPLETE_THREAD_ID}`);

    await expect(page.getByText(ARTIFACT_PATH)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByText(ARTIFACT_PATH).click();

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel.getByText("report.html")).toBeVisible();
    await expect(
      artifactsPanel.locator('iframe[title="Artifact preview"]'),
    ).toBeVisible();
  });

  test("renders markdown preview for an in-progress write artifact", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MARKDOWN_THREAD_ID,
          title: "Markdown artifact preview in progress",
          messages: writeFileMessages({
            path: MARKDOWN_ARTIFACT_PATH,
            content: "# Markdown draft\n\n- 测试内容 1\n- English term",
          }),
        },
      ],
    });

    await page.goto(`/workspace/chats/${MARKDOWN_THREAD_ID}`);

    await expect(page.getByText(MARKDOWN_ARTIFACT_PATH)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByText(MARKDOWN_ARTIFACT_PATH).click();

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel.getByText("report.md")).toBeVisible();
    await expect(artifactsPanel.getByText("Markdown draft")).toBeVisible();
    await expect(artifactsPanel.getByText("测试内容 1")).toBeVisible();
  });

  test("scrolls markdown artifact preview to heading anchors", async ({
    page,
  }) => {
    const filler = Array.from(
      { length: 40 },
      (_, index) => `填充段落 ${index + 1}`,
    ).join("\n\n");

    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: MARKDOWN_ANCHOR_THREAD_ID,
          title: "Markdown artifact anchor navigation",
          messages: writeFileMessages({
            path: MARKDOWN_ARTIFACT_PATH,
            content: [
              "# Report",
              "",
              "- [概述](#概述)",
              "",
              filler,
              "",
              "## 概述",
              "",
              "目标章节内容",
            ].join("\n"),
          }),
        },
      ],
    });

    await page.goto(`/workspace/chats/${MARKDOWN_ANCHOR_THREAD_ID}`);

    await expect(page.getByText(MARKDOWN_ARTIFACT_PATH)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByText(MARKDOWN_ARTIFACT_PATH).click();

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel.getByText("report.md")).toBeVisible();

    const targetHeading = artifactsPanel.locator("h2#概述");
    await expect(targetHeading).toHaveCount(1);
    await artifactsPanel.getByRole("link", { name: "概述" }).click();

    await expect
      .poll(async () =>
        targetHeading.evaluate((element) => {
          const panel = document.querySelector("#artifacts");
          if (!panel) {
            return false;
          }
          const panelRect = panel.getBoundingClientRect();
          const headingRect = element.getBoundingClientRect();
          return (
            headingRect.top >= panelRect.top &&
            headingRect.top <= panelRect.bottom
          );
        }),
      )
      .toBe(true);
  });

  test("renders code view for an in-progress non-preview write artifact", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: JSON_THREAD_ID,
          title: "JSON artifact code view in progress",
          messages: writeFileMessages({
            path: JSON_ARTIFACT_PATH,
            content:
              '{\n  "status": "draft",\n  "中文字段": "测试内容",\n  "count": 3\n}',
          }),
        },
      ],
    });

    await page.goto(`/workspace/chats/${JSON_THREAD_ID}`);

    await expect(page.getByText(JSON_ARTIFACT_PATH)).toBeVisible({
      timeout: 15_000,
    });
    await page.getByText(JSON_ARTIFACT_PATH).click();

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel.getByText("report.json")).toBeVisible();
    await expect(artifactsPanel.getByText('"status": "draft"')).toBeVisible();
    await expect(
      artifactsPanel.getByText('"中文字段": "测试内容"'),
    ).toBeVisible();
  });

  test("keeps an opened presented artifact in the header dropdown", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: PRESENTED_THREAD_ID,
          title: "Presented artifact dropdown history",
          messages: presentFilesMessages(),
          artifacts: [MARKDOWN_ARTIFACT_PATH],
        },
      ],
    });
    await page.route(
      `**/api/threads/${PRESENTED_THREAD_ID}/artifacts/mnt/user-data/outputs/presented-report.md`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "text/markdown",
          body: "# Presented Report\n\nGenerated content",
        }),
    );
    await page.route(
      `**/api/threads/${PRESENTED_THREAD_ID}/artifacts/artifact-fixtures/report.md`,
      (route) =>
        route.fulfill({
          status: 200,
          contentType: "text/markdown",
          body: "# Thread Report\n\nTracked artifact content",
        }),
    );

    await page.goto(`/workspace/chats/${PRESENTED_THREAD_ID}`);

    // Publishing files is passive: the files stay visible in the conversation,
    // but the right-side panel opens only after an explicit user action.
    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "true");

    // The file card in the message list shows the basename only.
    const presentedFileCard = page.locator(
      `[data-artifact-filepath="${PRESENTED_ARTIFACT_PATH}"]`,
    );
    await expect(presentedFileCard).toBeVisible({ timeout: 15_000 });
    await presentedFileCard.click();

    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "false");
    await expect(artifactsPanel.getByText("presented-report.md")).toBeVisible();
    await expect(artifactsPanel.getByText("Presented Report")).toBeVisible();

    const artifactSelect = artifactsPanel.getByRole("combobox");
    await artifactSelect.click();
    await page.getByRole("option", { name: "report.md", exact: true }).click();
    await expect(artifactsPanel.getByText("Thread Report")).toBeVisible();

    await artifactSelect.click();
    const presentedOption = page.getByRole("option", {
      name: "presented-report.md",
    });
    await expect(presentedOption).toBeVisible();
    await presentedOption.click();
    await expect(artifactsPanel.getByText("Presented Report")).toBeVisible();
  });

  test("opens a message PPTX link through its paired PDF preview", async ({
    page,
  }) => {
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: PRESENTED_PPTX_THREAD_ID,
          title: "PowerPoint artifact preview",
          messages: [
            {
              type: "human",
              id: "msg-human-present-pptx",
              content: [{ type: "text", text: "Create a PowerPoint" }],
            },
            {
              type: "ai",
              id: "msg-ai-present-pptx",
              content: `[${PRESENTED_PPTX_FILENAME}](/api/threads/${PRESENTED_PPTX_THREAD_ID}/artifacts${PRESENTED_PPTX_PATH})`,
              tool_calls: [
                {
                  id: "present-pptx-artifact",
                  name: "present_files",
                  args: { filepaths: [PRESENTED_PPTX_PATH] },
                },
              ],
            },
            {
              type: "tool",
              id: "msg-tool-present-pptx",
              name: "present_files",
              tool_call_id: "present-pptx-artifact",
              status: "success",
              content: "Published",
              artifact: {
                presented_files: [
                  PRESENTED_PPTX_PATH,
                  PRESENTED_PPTX_PREVIEW_PATH,
                ],
              },
            },
          ],
          artifacts: [PRESENTED_PPTX_PATH, PRESENTED_PPTX_PREVIEW_PATH],
        },
      ],
    });
    await page.route(`**/*111535.preview.pdf`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/pdf",
        headers: { "content-length": "14" },
        body:
          route.request().method() === "HEAD"
            ? undefined
            : "%PDF-1.4\n%%EOF",
      }),
    );

    await page.goto(`/workspace/chats/${PRESENTED_PPTX_THREAD_ID}`);
    const pptxLink = page.getByRole("link", {
      name: PRESENTED_PPTX_FILENAME,
    });
    await expect(pptxLink).toBeVisible({ timeout: 15_000 });
    await expect(pptxLink).toHaveAttribute("href", "#artifacts");
    await expect(pptxLink).toHaveAttribute(
      "data-artifact-href",
      new RegExp(
        `/api/threads/${PRESENTED_PPTX_THREAD_ID}/artifacts/mnt/user-data/outputs/${PRESENTED_PPTX_FILENAME.replace(".", "\\.")}$`,
      ),
    );
    await pptxLink.click();

    const previewFrame = page
      .locator("#artifacts")
      .locator('iframe[title="Artifact preview"]');
    await expect(previewFrame).toBeVisible();
    await expect(previewFrame).toHaveAttribute(
      "src",
      /中国海油勘探开发智能体平台研究报告_20260805_111535\.preview\.pdf$/,
    );
    await expect(
      page.locator(`[data-artifact-filepath="${PRESENTED_PPTX_PREVIEW_PATH}"]`),
    ).toHaveCount(0);

    const artifactsPanel = page.locator("#artifacts");
    const artifactSelect = artifactsPanel.getByRole("combobox");
    await artifactSelect.click();
    await expect(
      page.getByRole("option", {
        name: "中国海油勘探开发智能体平台研究报告_20260805_111535.preview.pdf",
      }),
    ).toHaveCount(0);
    await page.keyboard.press("Escape");

    const closeButton = artifactsPanel.getByRole("button", { name: "Close" });
    await expect(closeButton).toBeVisible();
    const panelBox = await artifactsPanel.boundingBox();
    const closeBox = await closeButton.boundingBox();
    expect(panelBox).not.toBeNull();
    expect(closeBox).not.toBeNull();
    expect(closeBox!.x + closeBox!.width).toBeLessThanOrEqual(
      panelBox!.x + panelBox!.width + 1,
    );
    await closeButton.click();
    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "true");
  });

  test("does not open a blank iframe for a stale recorded PPT preview", async ({
    page,
  }) => {
    const staleThreadId = "00000000-0000-0000-0000-000000003126";
    const staleDeck = "/mnt/user-data/outputs/历史汇报_20260805_120000.pptx";
    const stalePreview =
      "/mnt/user-data/outputs/历史汇报_20260805_120000.preview.pdf";
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: staleThreadId,
          title: "Stale PowerPoint preview",
          messages: [
            {
              type: "human",
              id: "msg-human-stale-pptx",
              content: [{ type: "text", text: "Create a PowerPoint" }],
            },
            {
              type: "ai",
              id: "msg-ai-stale-pptx",
              content: `[历史汇报_20260805_120000.pptx](/api/threads/${staleThreadId}/artifacts${staleDeck})`,
            },
          ],
          artifacts: [staleDeck, stalePreview],
        },
      ],
    });
    await page.route(
      `**/api/threads/${staleThreadId}/artifacts${stalePreview}`,
      (route) => route.fulfill({ status: 404, body: "missing" }),
    );

    await page.goto(`/workspace/chats/${staleThreadId}`);
    await page
      .getByRole("link", { name: "历史汇报_20260805_120000.pptx" })
      .click();

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "false");
    await expect(
      artifactsPanel.locator('iframe[title="Artifact preview"]'),
    ).toHaveCount(0);
    await expect(
      artifactsPanel
        .getByText("历史汇报_20260805_120000.pptx", { exact: true })
        .last(),
    ).toBeVisible();
    await expect(
      artifactsPanel.getByRole("link", { name: "Download" }),
    ).toBeVisible();
  });

  test("does not render or open artifacts from a failed present_files call", async ({
    page,
  }) => {
    const failedThreadId = "00000000-0000-0000-0000-000000003125";
    mockLangGraphAPI(page, {
      threads: [
        {
          thread_id: failedThreadId,
          title: "Failed PowerPoint delivery",
          messages: [
            {
              type: "human",
              id: "msg-human-failed-pptx",
              content: [{ type: "text", text: "Create a PowerPoint" }],
            },
            {
              type: "ai",
              id: "msg-ai-failed-pptx",
              content: "Publishing the files now.",
              tool_calls: [
                {
                  id: "present-failed-pptx",
                  name: "present_files",
                  args: {
                    filepaths: [
                      "/mnt/user-data/outputs/final.pptx",
                      "/mnt/user-data/outputs/final.report.json",
                    ],
                  },
                },
              ],
            },
            {
              type: "tool",
              id: "msg-tool-failed-pptx",
              name: "present_files",
              tool_call_id: "present-failed-pptx",
              status: "error",
              content: "Publishing was blocked",
            },
          ],
          artifacts: [],
        },
      ],
    });

    await page.goto(`/workspace/chats/${failedThreadId}`);

    const artifactsPanel = page.locator("#artifacts");
    await expect(artifactsPanel).toHaveAttribute("aria-hidden", "true");
    await expect(
      page.locator('[data-artifact-filepath$="final.pptx"]'),
    ).toHaveCount(0);
    await expect(
      page.locator('[data-artifact-filepath$="final.report.json"]'),
    ).toHaveCount(0);
  });
});
