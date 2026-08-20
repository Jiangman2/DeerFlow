import { describe, expect, it } from "@rstest/core";

import {
  appendGatewayTranscript,
  buildVoiceGatewayUrl,
  detectVoiceGatewaySupport,
  float32ToPcm16,
  parseVoiceGatewayEvent,
} from "@/core/voice-input/gateway-transcription";

describe("Gateway voice transcription helpers", () => {
  it("requires a secure browser media environment", () => {
    expect(
      detectVoiceGatewaySupport({
        isSecureContext: false,
        hasGetUserMedia: false,
        hasAudioContext: true,
        hasWebSocket: true,
      }),
    ).toEqual({ supported: false, reason: "insecure_context" });

    expect(
      detectVoiceGatewaySupport({
        isSecureContext: true,
        hasGetUserMedia: false,
        hasAudioContext: true,
        hasWebSocket: true,
      }),
    ).toEqual({ supported: false, reason: "media_devices_unavailable" });

    expect(
      detectVoiceGatewaySupport({
        isSecureContext: true,
        hasGetUserMedia: true,
        hasAudioContext: true,
        hasWebSocket: true,
      }),
    ).toEqual({ supported: true, reason: null });
  });

  it("uses the current DeerFlow origin and websocket scheme", () => {
    expect(buildVoiceGatewayUrl("https://deerflow.example/workspace/chats/new"))
      .toBe("wss://deerflow.example/api/voice-input/stream?language=auto");
    expect(buildVoiceGatewayUrl("http://127.0.0.1:2026/workspace/chats/new"))
      .toBe("ws://127.0.0.1:2026/api/voice-input/stream?language=auto");
  });

  it("converts normalized audio samples to little-endian PCM16", () => {
    const pcm = float32ToPcm16(new Float32Array([-1, -0.5, 0, 0.5, 1]));
    expect(Array.from(new Int16Array(pcm))).toEqual([
      -32768,
      -16384,
      0,
      16384,
      32767,
    ]);
  });

  it("parses only the Gateway event contract", () => {
    expect(
      parseVoiceGatewayEvent(
        JSON.stringify({
          type: "partial",
          text: "测试",
          full_text: "这是测试",
          language: "zh",
          is_final: false,
        }),
      ),
    ).toEqual({
      type: "partial",
      text: "测试",
      fullText: "这是测试",
      language: "zh",
      isFinal: false,
    });
    expect(parseVoiceGatewayEvent('{"type":"unexpected"}')).toBeNull();
    expect(parseVoiceGatewayEvent("not-json")).toBeNull();
  });

  it("appends live transcript without duplicating whitespace", () => {
    expect(appendGatewayTranscript("已有内容", "新的 语音")).toBe(
      "已有内容 新的 语音",
    );
    expect(appendGatewayTranscript("", " 新的 语音 ")).toBe("新的 语音");
  });
});
