import { describe, expect, test } from "@rstest/core";

import {
  createMarkdownLinkComponent,
  resolveLivePreviewURL,
} from "@/components/workspace/messages/markdown-link";

describe("resolveLivePreviewURL", () => {
  test("maps Sandbox loopback links to the current thread proxy", () => {
    expect(
      resolveLivePreviewURL("http://127.0.0.1:5050/?lang=zh-CN", "thread-1"),
    ).toBe("/api/threads/thread-1/live-preview/5050/?lang=zh-CN");
  });

  test("maps a bare Confirm UI URL followed by Chinese prose", () => {
    expect(
      resolveLivePreviewURL(
        "http://127.0.0.1:5050。这是流程的确认页面",
        "thread-1",
      ),
    ).toBe("/api/threads/thread-1/live-preview/5050/");
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
    const Link = createMarkdownLinkComponent("thread-1", undefined, "Preview");
    const element = Link({
      href: "http://127.0.0.1:5050/",
      children: "http://127.0.0.1:5050",
    });

    expect(element.props.href).toBe("/api/threads/thread-1/live-preview/5050/");
    expect(element.props.children).toBe("Preview");
  });

  test("thread-aware renderer rescues malformed Chinese autolinks without opening localhost", () => {
    const Link = createMarkdownLinkComponent("thread-1", undefined, "预览");
    const element = Link({
      href: "http://127.0.0.1:5050。这是流程的确认页面",
      children: "http://127.0.0.1:5050。这是流程的确认页面",
    });

    const previewLink = element.props.children[0];
    expect(previewLink.props.href).toBe(
      "/api/threads/thread-1/live-preview/5050/",
    );
    expect(previewLink.props.target).toBe("_blank");
    expect(element.props.children[1]).toBe("。这是流程的确认页面");
  });
});
