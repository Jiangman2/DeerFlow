import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it, rs } from "@rstest/core";
import { createElement, type ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MessageGroup } from "@/components/workspace/messages/message-group";
import { I18nContext } from "@/core/i18n/context";

rs.mock("@/components/workspace/artifacts", () => ({
  useArtifacts: () => ({
    artifacts: [],
    setArtifacts: () => undefined,
    selectedArtifact: null,
    select: () => undefined,
    deselect: () => undefined,
    open: false,
    setOpen: () => undefined,
  }),
}));

describe("MessageGroup", () => {
  it("renders unresolved streaming assistant text before a tool call arrives", () => {
    const html = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content: "I will inspect the source material first.",
        } as Message,
      ],
      { isLoading: true },
    );

    expect(html).toContain(">inspect</span>");
    expect(html).toContain(">source</span>");
    expect(html).toContain(">first</span>");
    expect(html).toContain(">.</span>");
  });

  it("renders assistant text attached to a tool-calling processing message", () => {
    const html = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "The browser action failed, so I will try another approach.",
        tool_calls: [
          {
            id: "call-1",
            name: "web_search",
            args: { query: "DeerFlow issue 4027" },
          },
        ],
      } as Message,
    ]);

    expect(html).toContain(
      "The browser action failed, so I will try another approach.",
    );
    expect(html).toContain("DeerFlow issue 4027");
  });

  it("keeps assistant text visible while older tool steps stay collapsed", () => {
    const html = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "The first tool failed; I will try a narrower search.",
        tool_calls: [
          {
            id: "call-1",
            name: "web_search",
            args: { query: "first hidden query" },
          },
        ],
      } as Message,
      {
        id: "tool-1",
        type: "tool",
        name: "web_search",
        tool_call_id: "call-1",
        content: "[]",
      } as Message,
      {
        id: "ai-2",
        type: "ai",
        content: "The second approach should reveal the missing context.",
        tool_calls: [
          {
            id: "call-2",
            name: "bash",
            args: {
              description: "Inspect message rendering",
              command: "rg assistantText frontend/src",
            },
          },
        ],
      } as Message,
    ]);

    expect(html).toContain(
      "The first tool failed; I will try a narrower search.",
    );
    expect(html).toContain(
      "The second approach should reveal the missing context.",
    );
    expect(html).not.toContain("first hidden query");
    expect(html).toContain("Inspect message rendering");
    expect(html).toContain("1 more step");
  });

  it("keeps content-only assistant text visible after a tool call while streaming", () => {
    const html = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content: "I will inspect the current implementation.",
          tool_calls: [
            {
              id: "call-1",
              name: "read_file",
              args: { path: "message-group.tsx" },
            },
          ],
        } as Message,
        {
          id: "tool-1",
          type: "tool",
          name: "read_file",
          tool_call_id: "call-1",
          content: "file contents",
        } as Message,
        {
          id: "ai-2",
          type: "ai",
          content: "Here is the final streamed answer.",
        } as Message,
      ],
      { isLoading: true },
    );

    expect(html).toContain(">final</span>");
    expect(html).toContain(">streamed</span>");
    expect(html).toContain(">answer</span>");
    expect(html).toContain(">.</span>");
  });

  it("renders answer text while the active message also carries reasoning", () => {
    const html = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content: "The visible answer is already streaming.",
          additional_kwargs: {
            reasoning_content: "Internal reasoning remains collapsible.",
          },
        } as Message,
      ],
      { isLoading: true },
    );

    expect(html).toContain(">visible</span>");
    expect(html).toContain(">answer</span>");
    expect(html).toContain("Thinking");
  });

  it("keeps tool-calling assistant text visible when reasoning is also present", () => {
    const html = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "I found a likely cause, so I will inspect the renderer next.",
        additional_kwargs: {
          reasoning_content: "Check how processing groups convert messages.",
        },
        tool_calls: [
          {
            id: "call-1",
            name: "bash",
            args: {
              description: "Inspect renderer conversion",
              command: "sed -n '720,780p' message-group.tsx",
            },
          },
        ],
      } as Message,
    ]);

    expect(html).toContain(
      "I found a likely cause, so I will inspect the renderer next.",
    );
    expect(html).toContain("Inspect renderer conversion");
    expect(html).toContain("1 more step");
    expect(html).not.toContain("Check how processing groups convert messages.");
  });

  it("rewrites Sandbox Live Preview links in tool-calling progress text", () => {
    const html = renderGroup(
      [
        {
          id: "ai-1",
          type: "ai",
          content:
            "Confirm at [http://127.0.0.1:5050](http://127.0.0.1:5050/).",
          tool_calls: [
            {
              id: "call-1",
              name: "bash",
              args: { command: "confirm_ui --wait-only" },
            },
          ],
        } as Message,
      ],
      "thread-1",
    );

    expect(html).toContain('href="/api/threads/thread-1/live-preview/5050/"');
    expect(html).not.toContain('href="http://127.0.0.1:5050/"');
  });

  it("keeps the first non-empty result for a tool call and skips task calls", () => {
    const html = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "call-fetch",
            name: "web_fetch",
            args: { url: "https://example.com" },
          },
          {
            id: "call-task",
            name: "task",
            args: { description: "Do not render this subagent call" },
          },
        ],
      } as Message,
      {
        id: "tool-empty",
        type: "tool",
        name: "web_fetch",
        tool_call_id: "call-fetch",
        content: "",
      } as Message,
      {
        id: "tool-first",
        type: "tool",
        name: "web_fetch",
        tool_call_id: "call-fetch",
        content: "# First fetched title\n\nFirst result.",
      } as Message,
      {
        id: "tool-later",
        type: "tool",
        name: "web_fetch",
        tool_call_id: "call-fetch",
        content: "# Later fetched title\n\nLater result.",
      } as Message,
    ]);

    expect(html).toContain("First fetched title");
    expect(html).not.toContain("Later fetched title");
    expect(html).not.toContain("Do not render this subagent call");
  });

  it("renders the earliest JSON tool result after an empty streamed update", () => {
    const html = renderGroup([
      {
        id: "ai-1",
        type: "ai",
        content: "",
        tool_calls: [
          {
            id: "call-search",
            name: "web_search",
            args: { query: "DeerFlow" },
          },
        ],
      } as Message,
      {
        id: "tool-empty",
        type: "tool",
        name: "web_search",
        tool_call_id: "call-search",
        content: "",
      } as Message,
      {
        id: "tool-first",
        type: "tool",
        name: "web_search",
        tool_call_id: "call-search",
        content: JSON.stringify([
          { title: "First source", url: "https://first.example" },
        ]),
      } as Message,
      {
        id: "tool-later",
        type: "tool",
        name: "web_search",
        tool_call_id: "call-search",
        content: JSON.stringify([
          { title: "Later source", url: "https://later.example" },
        ]),
      } as Message,
    ]);

    expect(html).toContain("First source");
    expect(html).toContain("https://first.example");
    expect(html).not.toContain("Later source");
    expect(html).not.toContain("https://later.example");
  });
});

// Tool args come from the model and results from search providers, so a
// prompt-injected URL must not become a navigable anchor. React only rewrites
// javascript: hrefs; local and OS-handler schemes would otherwise pass through.
// A blocked URL keeps the markdown path's "Unsafe link omitted" marker.
describe("MessageGroup tool links", () => {
  const unsafeUrls = [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "ms-msdt:/id PCWDiagnostic",
    "vscode://file/etc/passwd",
  ];

  it.each(unsafeUrls)("marks a web_fetch URL of %s as omitted", (url) => {
    const html = renderToolCall("web_fetch", { url });

    expect(html).toContain(`>${url}</span>`);
    expect(html).toContain(`title="Unsafe link scheme in ${url}"`);
    expect(unsafeMarkerCount(html)).toBe(1);
    expect(html).not.toContain("<a");
  });

  it.each(unsafeUrls)("marks a web_search result at %s as omitted", (url) => {
    const html = renderToolCall(
      "web_search",
      { query: "DeerFlow" },
      JSON.stringify([
        { title: "Safe source", url: "https://safe.example" },
        { title: "Injected source", url },
      ]),
    );

    expect(html).toContain('href="https://safe.example"');
    expect(html).toContain(">Injected source</span>");
    expect(unsafeMarkerCount(html)).toBe(1);
    expect(anchorCount(html)).toBe(1);
  });

  it.each(unsafeUrls)(
    "marks an image_search source at %s as omitted",
    (url) => {
      const html = renderToolCall(
        "image_search",
        { query: "DeerFlow" },
        JSON.stringify({
          results: [
            {
              title: "Injected image",
              source_url: url,
              thumbnail_url: "https://images.example/thumb.png",
              image_url: "https://images.example/full.png",
            },
          ],
        }),
      );

      expect(html).toContain('src="https://images.example/thumb.png"');
      expect(unsafeMarkerCount(html)).toBe(1);
      expect(html).not.toContain("<a");
    },
  );

  it("keeps linking web_fetch and image_search results with web URLs", () => {
    const fetchHtml = renderToolCall("web_fetch", {
      url: "https://example.com/page",
    });
    const imageHtml = renderToolCall(
      "image_search",
      { query: "DeerFlow" },
      JSON.stringify({
        results: [
          {
            title: "Image",
            source_url: "https://example.com/source",
            thumbnail_url: "https://images.example/thumb.png",
            image_url: "https://images.example/full.png",
          },
        ],
      }),
    );

    expect(fetchHtml).toContain('href="https://example.com/page"');
    expect(imageHtml).toContain('href="https://example.com/source"');
    expect(unsafeMarkerCount(fetchHtml + imageHtml)).toBe(0);
  });

  // Models occasionally emit non-string args, and the step renders mid-stream;
  // an object reaching the JSX would throw and take down the message list.
  it("renders a web_fetch step whose url arg is not a string", () => {
    const html = renderToolCall("web_fetch", {
      url: { href: "https://example.com/page" },
    });

    expect(html).toContain("View web page");
    expect(html).not.toContain("<a");
  });

  // Persisted or mid-stream tool calls can arrive without an args object;
  // every specialized branch reads args, so none may throw on its absence.
  it.each([
    "web_fetch",
    "web_search",
    "image_search",
    "ls",
    "read_file",
    "write_file",
    "str_replace",
    "bash",
    "ask_clarification",
    "write_todos",
    "browser_navigate",
    "mcp_lookup",
  ])("renders a %s step whose tool call has no args", (name) => {
    for (const args of [undefined, null]) {
      const render = () =>
        renderGroup([
          {
            id: "ai-1",
            type: "ai",
            content: "",
            tool_calls: [{ id: "call-1", name, args }],
          } as unknown as Message,
        ]);

      expect(render).not.toThrow();
    }
  });
});

function renderToolCall(
  name: string,
  args: Record<string, unknown>,
  content?: string,
) {
  const messages: Message[] = [
    {
      id: "ai-1",
      type: "ai",
      content: "",
      tool_calls: [{ id: "call-1", name, args }],
    } as Message,
  ];
  if (content !== undefined) {
    messages.push({
      id: "tool-1",
      type: "tool",
      name,
      tool_call_id: "call-1",
      content,
    } as Message);
  }
  return renderGroup(messages);
}

function unsafeMarkerCount(html: string) {
  return html.split('aria-label="Unsafe link omitted"').length - 1;
}

function anchorCount(html: string) {
  return html.match(/<a\s/g)?.length ?? 0;
}
function renderGroup(
  messages: Message[],
  props: Omit<ComponentProps<typeof MessageGroup>, "messages"> | string = {},
) {
  const normalizedProps =
    typeof props === "string" ? { threadId: props } : props;
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      {
        value: {
          locale: "en-US",
          setLocale: () => undefined,
        },
      },
      createElement(MessageGroup, { ...normalizedProps, messages }),
    ),
  );
}
