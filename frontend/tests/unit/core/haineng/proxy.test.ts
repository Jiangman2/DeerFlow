import { afterEach, beforeEach, expect, test, rs } from "@rstest/core";

rs.mock("@/core/auth/server", () => ({ getServerSideUser: rs.fn() }));
import { POST } from "@/app/api/haineng/chat/route";
import { getServerSideUser } from "@/core/auth/server";
import { openSession } from "@/core/haineng/server/conversation";

const secret = "test-only-platform-signing-secret-1234567890";
const oldSecret = process.env.HAINENG_CONVERSATION_SECRET;
function login(id: string) {
  rs.mocked(getServerSideUser).mockResolvedValue({
    tag: "authenticated",
    user: {
      id,
      email: `${id}@example.com`,
      system_role: "user",
      needs_setup: false,
    },
  });
}
function request(
  input: Record<string, unknown> = { content: "你好" },
  origin = "https://platform.example.com",
) {
  return new Request("https://platform.example.com/api/haineng/chat", {
    method: "POST",
    headers: { Origin: origin, "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}
beforeEach(() => {
  process.env.HAINENG_CONVERSATION_SECRET = secret;
  login("alice");
});
afterEach(() => {
  rs.restoreAllMocks();
  if (oldSecret === undefined) delete process.env.HAINENG_CONVERSATION_SECRET;
  else process.env.HAINENG_CONVERSATION_SECRET = oldSecret;
});

test("normal users need no test whitelist or personal cookie, streamed IDs bind to owner", async () => {
  const fetcher = rs
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(
      new Response(
        'data:{"header":{"code":0},"payload":{"sessionId":"up-1","data":{"choices":{"text":"回复","sessionId":"up-1"}}}}\n\ndata:{"header":{"code":10000}}\n\n',
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    );
  const response = await POST(request());
  expect(response.status).toBe(200);
  const text = await response.text();
  const frame = JSON.parse(text.split("\n\n")[0]!.slice(5)) as {
    payload: { sessionId: string };
  };
  expect(openSession(frame.payload.sessionId, "alice", secret)).toBe("up-1");
  const opts = fetcher.mock.calls[0]?.[1];
  expect(new Headers(opts?.headers).has("Cookie")).toBe(false);
  login("bob");
  const denied = await POST(
    request({ content: "继续", sessionId: frame.payload.sessionId }),
  );
  expect(denied.status).toBe(403);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("returns real upstream auth errors including HTTP 200 login envelope", async () => {
  const fetcher = rs.spyOn(globalThis, "fetch");
  for (const code of [401, 403, 302]) {
    fetcher.mockResolvedValueOnce(new Response(null, { status: code }));
    expect((await POST(request())).status).toBe(code === 302 ? 401 : code);
  }
  fetcher.mockResolvedValueOnce(
    Response.json({ ret: 302, redirectUrl: "https://uap.example.com" }),
  );
  expect((await POST(request())).status).toBe(401);
});

test("keeps Origin and platform authentication gates", async () => {
  const fetcher = rs.spyOn(globalThis, "fetch");
  expect(
    (await POST(request(undefined, "https://other.example.com"))).status,
  ).toBe(403);
  rs.mocked(getServerSideUser).mockResolvedValue({ tag: "unauthenticated" });
  expect((await POST(request())).status).toBe(401);
  expect(fetcher).not.toHaveBeenCalled();
});
