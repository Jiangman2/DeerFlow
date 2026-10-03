import { readFileSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "@rstest/core";

describe("MarkdownContent bounded streaming cadence", () => {
  it("uses the official deferred 50ms bounded reveal policy", () => {
    const source = readFileSync(
      path.resolve(
        __dirname,
        "../../../../../src/components/workspace/messages/markdown-content.tsx",
      ),
      "utf8",
    );

    expect(source).toContain("useDeferredValue");
    expect(source).toContain("SMOOTH_REVEAL_CADENCE_MS = 50");
    expect(source).toContain("SMOOTH_REVEAL_MIN_CHARS_PER_COMMIT = 64");
    expect(source).toContain("useSmoothStreamingContent");
  });
});
