import { expect, test } from "@rstest/core";

import { resolveHainengAuthentication } from "@/core/haineng/server/auth";
import {
  bindConversationStream,
  openSession,
  sealSession,
} from "@/core/haineng/server/conversation";

test("authentication extension point does not borrow a shared identity", async () => {
  expect(await resolveHainengAuthentication({ id: "alice" })).toEqual({
    headers: {},
  });
  expect(await resolveHainengAuthentication({ id: "bob" })).toEqual({
    headers: {},
  });
});

test("continuation tokens cannot be forged or reused by another user", () => {
  const secret = "test-secret";
  const token = sealSession("upstream-id", "alice", secret);
  expect(openSession(token, "alice", secret)).toBe("upstream-id");
  expect(() => openSession(token, "bob", secret)).toThrow();
  expect(() => openSession(`${token}x`, "alice", secret)).toThrow();
  expect(() => openSession("raw-upstream-id", "alice", secret)).toThrow();
});

test("binds all session ID locations across split UTF-8 and CRLF frames", async () => {
  const encoder = new TextEncoder();
  const raw =
    'data:{"header":{"code":0},"payload":{"sessionId":"s","data":{"sessionId":"s","choices":{"sessionId":"s","text":"你好"}}}}\r\n\r\ndata:{"header":{"code":10000}}\r\n\r\n';
  const source = new ReadableStream<Uint8Array>({
    start(c) {
      for (const b of encoder.encode(raw)) c.enqueue(Uint8Array.of(b));
      c.close();
    },
  });
  const text = await new Response(
    bindConversationStream(source, "alice", "secret"),
  ).text();
  const json = JSON.parse(text.split("\n\n")[0]!.slice(5)) as {
    payload: {
      sessionId: string;
      data: { sessionId: string; choices: { sessionId: string; text: string } };
    };
  };
  expect(json.payload.data.choices.text).toBe("你好");
  for (const token of [
    json.payload.sessionId,
    json.payload.data.sessionId,
    json.payload.data.choices.sessionId,
  ])
    expect(openSession(token, "alice", "secret")).toBe("s");
  expect(text).toContain('"code":10000');
});
