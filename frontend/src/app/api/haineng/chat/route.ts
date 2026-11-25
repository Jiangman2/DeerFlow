import { getServerSideUser } from "@/core/auth/server";
import { resolveHainengAuthentication } from "@/core/haineng/server/auth";
import {
  conversationSecret,
  openSession,
  bindConversationStream,
} from "@/core/haineng/server/conversation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function error(message: string, status: number) {
  return Response.json({ error: message }, { status });
}

export async function POST(request: Request) {
  const origin = request.headers.get("origin");
  const appOrigin =
    process.env.HAINENG_APP_ORIGIN ?? new URL(request.url).origin;
  if (!origin || origin !== appOrigin) return error("请求来源不匹配", 403);
  const auth = await getServerSideUser();
  if (auth.tag !== "authenticated" || auth.user.is_disabled)
    return error("请先登录智能体平台", 401);
  let input: { content?: unknown; sessionId?: unknown };
  try {
    input = (await request.json()) as typeof input;
  } catch {
    return error("请求正文不是有效 JSON", 400);
  }
  if (
    typeof input.content !== "string" ||
    !input.content.trim() ||
    input.content.length > 20000
  ) {
    return error("请输入 1–20000 字的问题", 400);
  }
  if (
    input.sessionId !== undefined &&
    (typeof input.sessionId !== "string" || input.sessionId.length > 4096)
  ) {
    return error("会话标识无效", 400);
  }
  let secret: string;
  try {
    secret = conversationSecret();
  } catch {
    return error("平台会话签名密钥未配置，请联系管理员", 503);
  }
  let sessionId: string | undefined;
  if (typeof input.sessionId === "string") {
    try {
      sessionId = openSession(input.sessionId, auth.user.id, secret);
    } catch {
      return error("对话标识无效或不属于当前用户，请新建对话", 403);
    }
  }
  try {
    const upstreamAuth = await resolveHainengAuthentication(auth.user);
    const url = new URL(
      process.env.HAINENG_CHAT_URL ??
        "https://heq.cnooc.com.cn/agent/unified-entrance/v1/unifiedChat",
    );
    if (url.protocol !== "https:" || url.username || url.password)
      return error("海能地址配置无效", 503);
    const upstream = await fetch(url, {
      method: "POST",
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(600000)]),
      headers: {
        "Content-Type": "application/json",
        Accept: "*/*",
        ...upstreamAuth.headers,
      },
      body: JSON.stringify({
        content: input.content.trim(),
        sessionId,
        chatType: "INTENTION_CLASSIFY",
        accessType: "WEB",
        enableWebSearch: false,
        enableDeepThinking: false,
      }),
    });
    if (upstream.status >= 300 && upstream.status < 400)
      return error("海能上游要求身份认证，当前用户尚无有效海能凭据", 401);
    if (upstream.status === 401 || upstream.status === 403)
      return error("海能上游认证或授权失败", upstream.status);
    if (!upstream.ok) return error(`海能请求失败（${upstream.status}）`, 502);
    if (
      !(upstream.headers.get("content-type") ?? "").includes(
        "text/event-stream",
      )
    ) {
      if (
        (upstream.headers.get("content-type") ?? "").includes(
          "application/json",
        )
      ) {
        const result = (await upstream.json()) as { ret?: number };
        if (result.ret === 302 || result.ret === 401 || result.ret === 403)
          return error(
            "海能上游要求身份认证，当前用户尚无有效海能凭据",
            result.ret === 403 ? 403 : 401,
          );
      } else {
        await upstream.body?.cancel();
      }
      return error("海能未返回事件流，请检查登录态及上游配置", 502);
    }
    return new Response(
      upstream.body
        ? bindConversationStream(upstream.body, auth.user.id, secret)
        : null,
      {
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-cache, no-store",
          "X-Accel-Buffering": "no",
        },
      },
    );
  } catch {
    return error("无法连接海能服务，请检查网络、DNS、证书或超时", 502);
  }
}
