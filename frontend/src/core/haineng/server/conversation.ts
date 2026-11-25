import { createHmac, timingSafeEqual } from "node:crypto";

export function conversationSecret() {
  const secret =
    process.env.HAINENG_CONVERSATION_SECRET ?? process.env.BETTER_AUTH_SECRET;
  if (!secret || secret.length < 32)
    throw new Error(
      "Configure a stable platform conversation secret (at least 32 characters)",
    );
  return secret;
}
export function sealSession(sessionId: string, userId: string, secret: string) {
  const data = Buffer.from(JSON.stringify({ sessionId, userId })).toString(
    "base64url",
  );
  const signature = createHmac("sha256", secret)
    .update(data)
    .digest("base64url");
  return `${data}.${signature}`;
}
export function openSession(
  token: string,
  userId: string,
  secret: string,
): string {
  const [data, signature, extra] = token.split(".");
  if (!data || !signature || extra)
    throw new Error("Invalid conversation token");
  const expected = createHmac("sha256", secret).update(data).digest();
  const actual = Buffer.from(signature, "base64url");
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected))
    throw new Error("Invalid conversation token");
  const parsed = JSON.parse(Buffer.from(data, "base64url").toString()) as {
    sessionId?: unknown;
    userId?: unknown;
  };
  if (
    parsed.userId !== userId ||
    typeof parsed.sessionId !== "string" ||
    !parsed.sessionId
  )
    throw new Error("Conversation belongs to another user");
  return parsed.sessionId;
}

/** Bind every upstream continuation ID to its platform owner before returning it. */
export function bindConversationStream(
  body: ReadableStream<Uint8Array>,
  userId: string,
  secret: string,
) {
  const decoder = new TextDecoder();
  const encoder = new TextEncoder();
  let buffer = "";
  function frame(text: string) {
    const data = text
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (!data) return text + "\n\n";
    const json = JSON.parse(data) as {
      payload?: {
        sessionId?: string;
        data?: { sessionId?: string; choices?: { sessionId?: string } };
      };
    };
    for (const target of [
      json.payload,
      json.payload?.data,
      json.payload?.data?.choices,
    ]) {
      if (target?.sessionId)
        target.sessionId = sealSession(target.sessionId, userId, secret);
    }
    return `data:${JSON.stringify(json)}\n\n`;
  }
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(value, controller) {
        buffer += decoder.decode(value, { stream: true });
        let separator: RegExpExecArray | null;
        while ((separator = /\r?\n\r?\n/.exec(buffer))) {
          controller.enqueue(
            encoder.encode(frame(buffer.slice(0, separator.index))),
          );
          buffer = buffer.slice(separator.index + separator[0].length);
        }
      },
      flush(controller) {
        buffer += decoder.decode();
        if (buffer.trim()) controller.enqueue(encoder.encode(frame(buffer)));
      },
    }),
  );
}
