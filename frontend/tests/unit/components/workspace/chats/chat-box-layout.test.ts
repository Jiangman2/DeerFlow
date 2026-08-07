import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/chats/chat-box.tsx"),
  "utf8",
);

describe("desktop artifact panel layout", () => {
  test("fills the complete right grid column without a container-unit gap", () => {
    expect(source).toContain(
      '"h-full w-full transition-opacity duration-[280ms] ease-out motion-reduce:transition-none"',
    );
    expect(source).not.toContain("w-[40cqw]");
  });
});
