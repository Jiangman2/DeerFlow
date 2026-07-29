import { expect, test } from "@rstest/core";

import {
  isStandardWeKnoraPath,
  WEKNORA_EMBED_PATH,
} from "@/core/weknora/embed";

test("uses the same-origin native WeKnora knowledge entry", () => {
  expect(WEKNORA_EMBED_PATH).toBe("/platform/knowledge-bases");
});

test("ordinary embedded users can use agents but not publish integrations", () => {
  expect(isStandardWeKnoraPath("/platform/creatChat")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/chat/chat-1")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/knowledge-bases")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/knowledge-bases/kb-1")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/organizations")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/agents")).toBe(true);
  expect(isStandardWeKnoraPath("/platform/integrations")).toBe(false);
  expect(isStandardWeKnoraPath("/platform/settings")).toBe(false);
  expect(isStandardWeKnoraPath("/platform/system/settings")).toBe(false);
});
