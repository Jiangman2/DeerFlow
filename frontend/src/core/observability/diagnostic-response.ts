// Bound the decoded response before JSON.parse, including chunked responses
// from older Gateways that do not yet provide a compact diagnostic preview.
export const DIAGNOSTIC_BYTE_LIMIT = 2 * 1024 * 1024;

export async function readDiagnosticResponse<T>(
  response: Response,
): Promise<T> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("错误详情响应为空，请稍后重试");
  let complete = false;
  try {
    if (
      Number(response.headers.get("content-length")) > DIAGNOSTIC_BYTE_LIMIT
    ) {
      throw new Error("错误详情过大，已停止加载以保护页面，请联系管理员");
    }
    const decoder = new TextDecoder();
    let bytes = 0;
    let text = "";
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > DIAGNOSTIC_BYTE_LIMIT) {
        throw new Error("错误详情过大，已停止加载以保护页面，请联系管理员");
      }
      text += decoder.decode(chunk.value, { stream: true });
    }
    text += decoder.decode();
    if (!response.ok)
      throw new Error(`错误详情加载失败（HTTP ${response.status}）`);
    const result = JSON.parse(text) as T;
    complete = true;
    return result;
  } finally {
    if (!complete) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
