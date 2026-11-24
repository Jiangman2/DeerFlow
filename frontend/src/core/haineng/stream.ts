export interface HainengEvent {
  header?: { code?: number; message?: string };
  payload?: { sessionId?: string; messageId?: string; data?: {
    sessionId?: string; messageId?: string;
    choices?: { text?: string; think?: string; sessionId?: string; messageId?: string };
  } };
}

/** SSE events may span arbitrary network chunks, including UTF-8 boundaries. */
export async function consumeHainengStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: HainengEvent) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let ended = false;
  function processFrame(frame: string) {
    const data = frame.split(/\r?\n/).filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, "")).join("\n");
    if (!data) return;
    const event = JSON.parse(data) as HainengEvent;
    onEvent(event);
    const code = event.header?.code;
    if (code === 10000) { ended = true; return; }
    if (code !== 0 && code !== 2) {
      throw new Error(event.header?.message ?? "海能返回业务错误");
    }
  }
  try {
    while (!ended) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let boundary: RegExpExecArray | null;
      while (!ended && (boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        processFrame(frame);
      }
      if (done) {
        if (!ended && buffer.trim()) processFrame(buffer);
        break;
      }
    }
    if (!ended) throw new Error("回答连接提前中断，已保留收到的内容，请重试。");
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
