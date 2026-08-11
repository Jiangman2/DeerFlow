import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, test } from "@rstest/core";

const source = readFileSync(
  resolve(process.cwd(), "src/components/workspace/chats/chat-box.tsx"),
  "utf8",
);
const artifactsContextSource = readFileSync(
  resolve(process.cwd(), "src/components/workspace/artifacts/context.tsx"),
  "utf8",
);

describe("desktop artifact panel layout", () => {
  test("fills the complete right grid column without a container-unit gap", () => {
    expect(source).toContain(
      '"h-full w-full transition-opacity duration-[280ms] ease-out motion-reduce:transition-none"',
    );
    expect(source).not.toContain("w-[40cqw]");
  });

  test("recovers persisted artifacts when stream hydration restores messages first", () => {
    expect(source).toContain("getAPIClient(undefined, threadId)");
    expect(source).toContain("threads.getState(threadId)");
    expect(source).toContain("extractArtifactsFromStateSnapshot(state)");
  });

  test("keeps the artifact overlay below the chat header without resizing chat", () => {
    expect(source).toContain('artifactsOverlayOpen\n          ? "relative"');
    expect(source).toContain("top-12 right-0 bottom-0");
    expect(source).toContain("w-[min(560px,calc(100%-1rem))]");
    expect(source).toContain('data-testid="artifact-overlay-panel"');
    expect(source).not.toContain("inset-y-0 right-0 z-50");
    expect(source).not.toContain("w-[min(600px,38vw)]");
  });

  test("does not collapse the workspace sidebar when selecting a file", () => {
    expect(artifactsContextSource).not.toContain("useSidebar");
    expect(artifactsContextSource).not.toContain("setSidebarOpen");
  });
});
