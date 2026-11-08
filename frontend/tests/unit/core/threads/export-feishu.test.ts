import type { Message } from "@langchain/langgraph-sdk";
import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  sendThreadArtifactsToFeishu,
  sendThreadAsMarkdownToFeishu,
} from "@/core/threads/export";
import type { AgentThread } from "@/core/threads/types";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: status >= 400 ? "Bad Request" : "OK",
    headers: { "Content-Type": "application/json" },
  });
}

function makeThread(): AgentThread {
  return {
    thread_id: "thread-1",
    created_at: "2026-05-21T00:00:00Z",
    updated_at: "2026-05-21T00:00:00Z",
    metadata: { title: "测试对话" },
    status: "idle",
    values: { messages: [], title: "测试对话" },
  } as unknown as AgentThread;
}

function human(content: string): Message {
  return { id: `h-${content}`, type: "human", content } as Message;
}

function requestBody(call: unknown): Record<string, unknown> {
  const init = (call as [string, RequestInit])[1];
  const raw = init.body;
  if (typeof raw !== "string") {
    throw new Error("expected a JSON string request body");
  }
  return JSON.parse(raw) as Record<string, unknown>;
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("sendThreadAsMarkdownToFeishu", () => {
  test("omits the target so the backend uses the verified personal chat", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        sent: true,
        filename: "测试对话.md",
        target_chat_id: "oc-p2p-chat",
        target_chat_type: "p2p",
        target_name: null,
      }),
    );

    const result = await sendThreadAsMarkdownToFeishu(makeThread(), [
      human("hello"),
    ]);

    expect(mockedFetch).toHaveBeenCalledTimes(1);
    const [url] = mockedFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/channels/feishu/export-thread");
    const body = requestBody(mockedFetch.mock.calls[0]);
    expect(body.thread_id).toBe("thread-1");
    expect(body.filename).toBe("测试对话.md");
    expect(body.content).toContain("hello");
    expect(body).not.toHaveProperty("target_chat_id");
    expect(result.target_chat_id).toBe("oc-p2p-chat");
    expect(result.target_chat_type).toBe("p2p");
  });

  test("forwards an explicitly chosen group chat", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        sent: true,
        filename: "测试对话.md",
        target_chat_id: "oc-group-chat",
        target_chat_type: "group",
        target_name: "项目群",
      }),
    );

    const result = await sendThreadAsMarkdownToFeishu(
      makeThread(),
      [human("hello")],
      "oc-group-chat",
    );

    expect(requestBody(mockedFetch.mock.calls[0]).target_chat_id).toBe(
      "oc-group-chat",
    );
    expect(result.target_name).toBe("项目群");
    expect(result.target_chat_type).toBe("group");
  });

  test("surfaces the gateway error detail", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(400, {
        detail:
          "未找到海能 Work 私聊绑定，请先在海能 Work 私聊中发送连接码完成绑定后再试。",
      }),
    );

    await expect(
      sendThreadAsMarkdownToFeishu(makeThread(), [human("hello")]),
    ).rejects.toThrow("未找到海能 Work 私聊绑定");
  });
});

describe("sendThreadArtifactsToFeishu", () => {
  test("sends artifact paths without a target by default", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        sent: true,
        filenames: ["report.md"],
        skipped: [],
        target_chat_id: "oc-p2p-chat",
        target_chat_type: "p2p",
        target_name: null,
      }),
    );

    const result = await sendThreadArtifactsToFeishu("thread-1", [
      "/mnt/user-data/outputs/report.md",
    ]);

    const [url] = mockedFetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/backend/api/channels/feishu/export-artifacts");
    const body = requestBody(mockedFetch.mock.calls[0]);
    expect(body.thread_id).toBe("thread-1");
    expect(body.artifact_paths).toEqual(["/mnt/user-data/outputs/report.md"]);
    expect(body).not.toHaveProperty("target_chat_id");
    expect(result.filenames).toEqual(["report.md"]);
  });

  test("forwards an explicitly chosen group chat", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        sent: true,
        filenames: ["report.md"],
        skipped: ["original.txt"],
        target_chat_id: "oc-group-chat",
        target_chat_type: "group",
        target_name: "项目群",
      }),
    );

    const result = await sendThreadArtifactsToFeishu(
      "thread-1",
      ["/mnt/user-data/outputs/report.md"],
      "oc-group-chat",
    );

    expect(requestBody(mockedFetch.mock.calls[0]).target_chat_id).toBe(
      "oc-group-chat",
    );
    expect(result.skipped).toEqual(["original.txt"]);
  });
});
