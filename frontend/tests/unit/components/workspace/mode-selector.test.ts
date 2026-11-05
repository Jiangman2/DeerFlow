import { expect, test } from "@rstest/core";

import { resolveAgentMode } from "@/components/workspace/mode-selector";

test("defaults missing chat mode to flash for thinking-capable models", () => {
  expect(resolveAgentMode(undefined, true)).toBe("flash");
});

test("always-thinking providers never present flash as an effective mode", () => {
  expect(resolveAgentMode(undefined, true, true)).toBe("thinking");
  expect(resolveAgentMode("flash", true, true)).toBe("thinking");
  expect(resolveAgentMode("ultra", true, true)).toBe("ultra");
});

test("preserves an explicit saved chat mode", () => {
  expect(resolveAgentMode("pro", true)).toBe("pro");
  expect(resolveAgentMode("ultra", true)).toBe("ultra");
});

test("forces flash when the selected model does not support thinking", () => {
  expect(resolveAgentMode("pro", false)).toBe("flash");
});
