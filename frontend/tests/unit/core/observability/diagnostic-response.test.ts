import { describe, expect, it } from "@rstest/core";

import {
  DIAGNOSTIC_BYTE_LIMIT,
  readDiagnosticResponse,
} from "@/core/observability/diagnostic-response";

describe("bounded diagnostic response", () => {
  it("reads normal UTF-8 JSON", async () => {
    expect(
      await readDiagnosticResponse(
        new Response(JSON.stringify({ text: "错误摘要" })),
      ),
    ).toEqual({ text: "错误摘要" });
  });
  it("rejects a declared oversized body without parsing it", async () => {
    await expect(
      readDiagnosticResponse(
        new Response("not json", {
          headers: { "content-length": String(DIAGNOSTIC_BYTE_LIMIT + 1) },
        }),
      ),
    ).rejects.toThrow("错误详情过大");
  });
  it("counts actual streamed bytes and cancels chunked overflow", async () => {
    let cancelled = false;
    const response = new Response(
      new ReadableStream({
        pull(controller) {
          controller.enqueue(new Uint8Array(256 * 1024));
        },
        cancel() {
          cancelled = true;
        },
      }),
    );
    await expect(readDiagnosticResponse(response)).rejects.toThrow(
      "错误详情过大",
    );
    expect(cancelled).toBe(true);
  });
  it("does not display a raw server error body", async () => {
    await expect(
      readDiagnosticResponse(new Response("private-error", { status: 500 })),
    ).rejects.toThrow("HTTP 500");
  });
});
