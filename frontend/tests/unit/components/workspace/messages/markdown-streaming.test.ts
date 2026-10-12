import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

describe("MarkdownContent bounded streaming cadence", () => {
  it("retains deferred 50ms rendering without imposing 64-character batches", () => {
    const source = readFileSync(
      path.resolve(
        __dirname,
        "../../../../../src/components/workspace/messages/markdown-content.tsx",
      ),
      "utf8",
    );

    const hook = readFileSync(
      path.resolve(
        __dirname,
        "../../../../../src/core/streamdown/use-smooth-streaming-text.ts",
      ),
      "utf8",
    );
    const buffer = readFileSync(
      path.resolve(
        __dirname,
        "../../../../../src/core/streamdown/streaming-text-buffer.ts",
      ),
      "utf8",
    );
    expect(source).toContain("useSmoothStreamingText");
    expect(hook).toContain("useDeferredValue");
    expect(buffer).toContain("STREAMING_TEXT_FRAME_MS = 50");
    expect(buffer).toContain("MAX_REVEAL_DELAY_MS = 300");
    expect(source).not.toContain("SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT");
  });
});
