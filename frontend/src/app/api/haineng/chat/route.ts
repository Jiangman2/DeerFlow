import { getServerSideUser } from "@/core/auth/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function error(message: string, status: number) {
  return Response.json({ error: message }, { status });
}

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) return error("请求来源不匹配", 403);
  const auth = await getServerSideUser();
  if (auth.tag !== "authenticated") return error("请先登录智能体平台", 401);
  // A temporary upstream SESSION represents exactly one Haineng user.
  const allowed = (process.env.HAINENG_TEST_USER_IDS ?? "").split(",").map((id) => id.trim());
  if (!allowed.includes(String(auth.user.id))) return error("当前用户未获海能联调权限", 403);
  const session = process.env.HAINENG_SESSION?.trim();
  if (!session || /[\r\n;]/.test(session)) return error("海能测试会话尚未配置", 503);
  let input: { content?: unknown; sessionId?: unknown };
  try { input = await request.json() as typeof input; }
  catch { return error("请求正文不是有效 JSON", 400); }
  if (typeof input.content !== "string" || !input.content.trim() || input.content.length > 20000) {
    return error("请输入 1–20000 字的问题", 400);
  }
  if (input.sessionId !== undefined && (typeof input.sessionId !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(input.sessionId))) {
    return error("会话标识无效", 400);
  }
  try {
    const url = new URL(process.env.HAINENG_CHAT_URL ?? "https://heq.cnooc.com.cn/agent/unified-entrance/v1/unifiedChat");
    if (url.protocol !== "https:" || url.username || url.password) return error("海能地址配置无效", 503);
    const upstream = await fetch(url, {
      method: "POST", redirect: "manual", cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(600000)]),
      headers: { "Content-Type": "application/json", Accept: "*/*", Cookie: `SESSION=${session}` },
      body: JSON.stringify({ content: input.content.trim(), sessionId: input.sessionId,
        chatType: "INTENTION_CLASSIFY", accessType: "WEB", enableWebSearch: false, enableDeepThinking: false }),
    });
    if (upstream.status >= 300 && upstream.status < 400) return error("海能登录态已失效，请更新服务端会话", 401);
    if (!upstream.ok) return error(`海能请求失败（${upstream.status}）`, 502);
    if (!(upstream.headers.get("content-type") ?? "").includes("text/event-stream")) {
      await upstream.body?.cancel();
      return error("海能未返回事件流，请检查 SESSION 是否过期及上游配置", 502);
    }
    return new Response(upstream.body, { headers: {
      "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-store",
      "X-Accel-Buffering": "no",
    } });
  } catch {
    return error("无法连接海能服务，请检查网络、DNS、证书或超时", 502);
  }
}
