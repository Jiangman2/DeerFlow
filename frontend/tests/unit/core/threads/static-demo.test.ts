import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "@rstest/core";

import { DEMO_THREAD_IDS } from "@/core/threads/static-demo";

describe("bundled static demo", () => {
  it("ships one real ppt-master run with its complete static preview bundle", () => {
    expect(DEMO_THREAD_IDS).toHaveLength(1);
    const threadId = DEMO_THREAD_IDS[0];
    const root = resolve(process.cwd(), "public/demo/threads", threadId);
    const data = JSON.parse(
      readFileSync(resolve(root, "thread.json"), "utf8"),
    ) as {
      values: {
        title: string;
        artifacts: string[];
        messages: Array<{
          additional_kwargs?: {
            ppt_master_task?: { steps?: unknown[] };
          };
        }>;
      };
    };

    expect(data.values.title).toContain("PPT 真实执行");
    const taskStepCounts = data.values.messages
      .map(
        (message) =>
          message.additional_kwargs?.ppt_master_task?.steps?.length ?? 0,
      )
      .filter((count) => count > 0);
    expect(taskStepCounts).toEqual([40, 154]);

    const pptx = data.values.artifacts.find((path) =>
      path.toLowerCase().endsWith(".pptx"),
    );
    expect(pptx).toBeDefined();
    const relativePptx = pptx!.replace(/^\/mnt\//, "");
    expect(existsSync(resolve(root, relativePptx))).toBe(true);

    const previewPdf = relativePptx.replace(/\.pptx$/i, ".preview.pdf");
    const previewHtml = relativePptx.replace(/\.pptx$/i, ".preview.html");
    expect(existsSync(resolve(root, previewPdf))).toBe(true);
    expect(existsSync(resolve(root, previewHtml))).toBe(true);

    const html = readFileSync(resolve(root, previewHtml), "utf8");
    const pageImages = [...html.matchAll(/<img src="([^"]+)"/g)].map(
      (match) => match[1]!,
    );
    expect(pageImages).toHaveLength(5);
    for (const pageImage of pageImages) {
      expect(existsSync(resolve(root, "user-data/outputs", pageImage))).toBe(
        true,
      );
    }
  });
});
