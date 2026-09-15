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
