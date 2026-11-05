import { expect, test } from "@rstest/core";

import {
  PORTAL_TOOLS,
  PORTAL_QUICK_TOOLS,
  PORTAL_MORE_TOOLS,
  PPT_ASSISTANT_TOOL,
  USER_GUIDE_URL,
} from "@/core/portal-links";

test("portal catalogue preserves the seven approved destinations and their hashes", () => {
  expect(PORTAL_TOOLS.map((tool) => tool.href)).toEqual([
    "https://ppt.cnooc.com.cn/aippt/home",
    "https://proofread.cnooc.com.cn/#/check",
    "https://translator.cnooc.com.cn/#/documentTranslation",
    "https://meeting.cnooc.com.cn/gateway/static/#/home/conference/index",
    "https://translator.cnooc.com.cn/#/wordToPdf?module=format",
    "https://meeting.cnooc.com.cn/gateway/static/indexzx.html#/home/officeAssistant/record",
    "https://vm.cnooc.com.cn/",
  ]);
  expect(new Set(PORTAL_TOOLS.map((tool) => tool.id)).size).toBe(7);
  expect(
    PORTAL_TOOLS.every((tool) => tool.artwork.startsWith("/images/")),
  ).toBe(true);
});

test("each destination has exactly one approved entry surface", () => {
  expect(PORTAL_QUICK_TOOLS.map((tool) => tool.id)).toEqual([
    "proofread",
    "translate",
  ]);
  expect(PORTAL_MORE_TOOLS.map((tool) => tool.id)).toEqual([
    "liveTranscribe",
    "convert",
    "recordTranscribe",
    "speech",
  ]);
  expect(PPT_ASSISTANT_TOOL.id).toBe("ppt");
  expect(
    new Set(
      [...PORTAL_QUICK_TOOLS, ...PORTAL_MORE_TOOLS, PPT_ASSISTANT_TOOL].map(
        (tool) => tool.id,
      ),
    ).size,
  ).toBe(PORTAL_TOOLS.length);
});

test("guide uses the approved Work document, not a guessed public endpoint", () => {
  expect(USER_GUIDE_URL).toBe(
    "https://hnwork.w.cnooc.com.cn/docx/doxkjqbGzuarsaBp3oeDtVmcBug",
  );
});
