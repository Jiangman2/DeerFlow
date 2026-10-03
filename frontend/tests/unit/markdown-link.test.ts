import { describe, expect, test } from "@rstest/core";

import {
  createMarkdownLinkComponent,
  resolveLivePreviewURL,
} from "@/components/workspace/messages/markdown-link";

describe("resolveLivePreviewURL", () => {
  test("maps Sandbox loopback links to the current thread proxy", () => {
    expect(
      resolveLivePreviewURL(
        "http://127.0.0.1:5050/?lang=zh-CN",
        "thread-1",
      ),
    ).toBe(
      "/api/threads/thread-1/live-preview/5050/?lang=zh-CN",
    );
  });

  test("does not rewrite ordinary external links or other ports", () => {
    expect(resolveLivePreviewURL("https://example.com", "thread-1")).toBeNull();
    expect(
      resolveLivePreviewURL("http://127.0.0.1:3000/", "thread-1"),
    ).toBeNull();
    expect(
      resolveLivePreviewURL("http://127.0.0.1:5050/", undefined),
    ).toBeNull();
  });

  test("thread-aware markdown renderer rewrites ordinary assistant messages", () => {
    const Link = createMarkdownLinkComponent(
      "thread-1",
      undefined,
      "Preview",
    );
    const element = Link({
      href: "http://127.0.0.1:5050/",
      children: "http://127.0.0.1:5050",
    });

    expect(element.props.href).toBe("/api/threads/thread-1/live-preview/5050/");
    expect(element.props.children).toBe("Preview");
  });
});
