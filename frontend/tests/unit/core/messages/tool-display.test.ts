import { describe, expect, it } from "@rstest/core";

import {
  toolDisplayRecord,
  toolDisplayText,
  toolWebHref,
} from "@/core/messages/tool-display";

describe("untrusted tool display values", () => {
  it.each([undefined, null, [], "query", 7, false])(
    "normalizes a non-record argument container %s",
    (value) => {
      expect(toolDisplayRecord(value)).toEqual({});
    },
  );

  it("keeps valid argument records and only returns text values", () => {
    const args = { query: "DeerFlow", url: { href: "https://example.com" } };
    expect(toolDisplayRecord(args)).toBe(args);
    expect(toolDisplayText(args, "query")).toBe("DeerFlow");
    expect(toolDisplayText(args, "url")).toBeUndefined();
    expect(toolDisplayText(args, "missing")).toBeUndefined();
  });

  it.each([
    undefined,
    null,
    {},
    [],
    7,
    "",
    "/api/files",
    "//example.com",
    "javascript:alert(1)",
    "file:///etc/passwd",
    "vscode://file/etc/passwd",
    "ms-msdt:/id PCWDiagnostic",
    "data:text/html,test",
    "https://",
    "https://user:password@example.com",
    "https://safe.example\\@evil.example",
    "https://safe.example\n/path",
    "https://safe.example\u0000/path",
  ])("does not navigate an unsafe or malformed URL %s", (value) => {
    expect(toolWebHref(value)).toBeNull();
  });

  it.each(["https://example.com", "http://example.com/page?q=1#section"])(
    "preserves the exact safe web URL %s",
    (value) => {
      expect(toolWebHref(value)).toBe(value);
    },
  );
});
