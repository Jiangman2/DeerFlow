export type VoiceGatewaySupportReason =
  | "insecure_context"
  | "media_devices_unavailable"
  | "audio_context_unavailable"
  | "websocket_unavailable";

export type VoiceGatewaySupport =
  | { supported: true; reason: null }
  | { supported: false; reason: VoiceGatewaySupportReason };

export type VoiceGatewayEvent =
  | {
      type: "ready";
      model: string;
      sampleRate: number;
    }
  | {
      type: "partial" | "final";
      text: string;
      fullText: string;
      language: string;
      isFinal: boolean;
    }
  | {
      type: "error";
      code: string;
      message: string;
    };

export type GatewayVoiceSessionOptions = {
  baseText: string;
  language?: string;
  onTranscript: (text: string) => void;
  onListeningChange: (listening: boolean) => void;
  onError: (code: string, message?: string) => void;
  locationHref?: string;
};

type AudioContextConstructor = new (
  options?: AudioContextOptions,
) => AudioContext;
type VoiceWindow = Window &
  typeof globalThis & {
    webkitAudioContext?: AudioContextConstructor;
  };

export function detectVoiceGatewaySupport(input?: {
  isSecureContext: boolean;
  hasGetUserMedia: boolean;
  hasAudioContext: boolean;
  hasWebSocket: boolean;
}): VoiceGatewaySupport {
  const capabilities =
    input ??
    ({
      isSecureContext:
        typeof window !== "undefined" && window.isSecureContext === true,
      hasGetUserMedia:
        typeof navigator !== "undefined" &&
        typeof navigator.mediaDevices?.getUserMedia === "function",
      hasAudioContext:
        typeof window !== "undefined" &&
        Boolean(
          window.AudioContext || (window as VoiceWindow).webkitAudioContext,
        ),
      hasWebSocket: typeof WebSocket !== "undefined",
    } satisfies Parameters<typeof detectVoiceGatewaySupport>[0]);

  if (!capabilities.isSecureContext) {
    return { supported: false, reason: "insecure_context" };
  }
  if (!capabilities.hasGetUserMedia) {
    return { supported: false, reason: "media_devices_unavailable" };
  }
  if (!capabilities.hasAudioContext) {
    return { supported: false, reason: "audio_context_unavailable" };
  }
  if (!capabilities.hasWebSocket) {
    return { supported: false, reason: "websocket_unavailable" };
  }
  return { supported: true, reason: null };
}

export function buildVoiceGatewayUrl(
  locationHref: string,
  language = "auto",
): string {
  const url = new URL("/api/voice-input/stream", locationHref);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("language", language || "auto");
  return url.toString();
}

export function appendGatewayTranscript(baseText: string, transcript: string) {
  const cleanTranscript = transcript.replace(/\s+/g, " ").trim();
  if (!cleanTranscript) {
    return baseText;
  }
  const cleanBase = baseText.trimEnd();
  return cleanBase ? `${cleanBase} ${cleanTranscript}` : cleanTranscript;
}

export function float32ToPcm16(input: Float32Array): ArrayBuffer {
  const output = new ArrayBuffer(input.length * Int16Array.BYTES_PER_ELEMENT);
  const view = new DataView(output);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index] ?? 0));
    const value = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    view.setInt16(index * 2, Math.round(value), true);
  }
  return output;
}

export function downsampleTo16Khz(
  input: Float32Array,
  sourceSampleRate: number,
): Float32Array {
  if (sourceSampleRate <= 0) {
    throw new Error("Invalid source sample rate");
  }
  if (sourceSampleRate === 16000) {
    return input;
  }
  if (sourceSampleRate < 16000) {
    throw new Error("Microphone sample rate is below 16 kHz");
  }

  const ratio = sourceSampleRate / 16000;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(outputLength);
  let inputOffset = 0;
  for (let outputOffset = 0; outputOffset < outputLength; outputOffset += 1) {
    const nextInputOffset = Math.min(
      input.length,
      Math.round((outputOffset + 1) * ratio),
    );
    let total = 0;
    let count = 0;
    for (; inputOffset < nextInputOffset; inputOffset += 1) {
      total += input[inputOffset] ?? 0;
      count += 1;
    }
    output[outputOffset] = count ? total / count : 0;
  }
  return output;
}

export function parseVoiceGatewayEvent(raw: string): VoiceGatewayEvent | null {
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!value || typeof value !== "object") {
    return null;
  }
  const event = value as Record<string, unknown>;
  if (event.type === "ready") {
    return {
      type: "ready",
      model: typeof event.model === "string" ? event.model : "",
      sampleRate:
        typeof event.sample_rate === "number" ? event.sample_rate : 16000,
    };
  }
  if (event.type === "partial" || event.type === "final") {
    return {
      type: event.type,
      text: typeof event.text === "string" ? event.text : "",
      fullText: typeof event.full_text === "string" ? event.full_text : "",
      language: typeof event.language === "string" ? event.language : "",
      isFinal: event.type === "final",
    };
  }
  if (event.type === "error") {
    return {
      type: "error",
      code: typeof event.code === "string" ? event.code : "unknown",
      message: typeof event.message === "string" ? event.message : "",
    };
  }
  return null;
}

export class GatewayVoiceSession {
  private readonly options: GatewayVoiceSessionOptions;
  private socket: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private audioContext: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private silentGain: GainNode | null = null;
  private stopping = false;
  private closed = false;
  private finalTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: GatewayVoiceSessionOptions) {
    this.options = options;
  }

  async start(): Promise<void> {
    const support = detectVoiceGatewaySupport();
    if (!support.supported) {
      this.options.onError(support.reason);
      return;
    }

    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
        video: false,
      });
      if (this.closed) {
        this.stopTracks();
        return;
      }

      const locationHref = this.options.locationHref ?? window.location.href;
      this.socket = new WebSocket(
        buildVoiceGatewayUrl(locationHref, this.options.language ?? "auto"),
      );
      this.socket.binaryType = "arraybuffer";
      this.socket.onmessage = (message) => this.handleMessage(message);
      this.socket.onerror = () => {
        if (!this.closed) {
          this.options.onError("network");
        }
      };
      this.socket.onclose = (event) => {
        if (!this.closed && !this.stopping && event.code !== 1000) {
          this.options.onError(
            event.code === 4401 ? "authentication_required" : "network",
          );
        }
        this.cleanup();
      };
    } catch (error) {
      this.cleanup();
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        this.options.onError("permission_denied");
      } else if (
        error instanceof DOMException &&
        ["NotFoundError", "NotReadableError", "OverconstrainedError"].includes(
          error.name,
        )
      ) {
        this.options.onError("microphone_unavailable");
      } else {
        this.options.onError("unknown");
      }
    }
  }

  stop(): void {
    if (this.closed || this.stopping) {
      return;
    }
    this.stopping = true;
    this.stopAudioGraph();
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(new ArrayBuffer(0));
      this.finalTimer = setTimeout(() => {
        if (!this.closed) {
          this.options.onError("final_timeout");
          this.abort();
        }
      }, 30_000);
    } else {
      this.abort();
    }
  }

  abort(): void {
    if (this.closed) {
      return;
    }
    this.closed = true;
    this.socket?.close(1000, "Voice input cancelled");
    this.cleanup();
  }

  private async beginAudio(): Promise<void> {
    if (!this.stream || this.closed) {
      return;
    }
    const VoiceAudioContext =
      window.AudioContext ?? (window as VoiceWindow).webkitAudioContext;
    if (!VoiceAudioContext) {
      this.options.onError("audio_context_unavailable");
      this.abort();
      return;
    }

    this.audioContext = new VoiceAudioContext({ latencyHint: "interactive" });
    await this.audioContext.resume();
    this.source = this.audioContext.createMediaStreamSource(this.stream);
    this.processor = this.audioContext.createScriptProcessor(4096, 1, 1);
    this.silentGain = this.audioContext.createGain();
    this.silentGain.gain.value = 0;
    this.processor.onaudioprocess = (event) => {
      if (this.socket?.readyState !== WebSocket.OPEN || this.stopping) {
        return;
      }
      const mono = event.inputBuffer.getChannelData(0);
      const downsampled = downsampleTo16Khz(
        mono,
        this.audioContext?.sampleRate ?? 48000,
      );
      this.socket.send(float32ToPcm16(downsampled));
    };
    this.source.connect(this.processor);
    this.processor.connect(this.silentGain);
    this.silentGain.connect(this.audioContext.destination);
    this.options.onListeningChange(true);
  }

  private handleMessage(message: MessageEvent<unknown>): void {
    if (typeof message.data !== "string") {
      this.options.onError("malformed_response");
      this.abort();
      return;
    }
    const event = parseVoiceGatewayEvent(message.data);
    if (!event) {
      this.options.onError("malformed_response");
      this.abort();
      return;
    }
    if (event.type === "ready") {
      void this.beginAudio();
      return;
    }
    if (event.type === "partial" || event.type === "final") {
      this.options.onTranscript(
        appendGatewayTranscript(
          this.options.baseText,
          event.fullText || event.text,
        ),
      );
      if (event.type === "final") {
        this.closed = true;
        this.socket?.close(1000, "Transcription complete");
        this.cleanup();
      }
      return;
    }
    if (event.type === "error") {
      this.options.onError(event.code, event.message);
      this.abort();
    }
  }

  private stopAudioGraph(): void {
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
      this.processor = null;
    }
    this.source?.disconnect();
    this.source = null;
    this.silentGain?.disconnect();
    this.silentGain = null;
    this.stopTracks();
    if (this.audioContext) {
      void this.audioContext.close();
      this.audioContext = null;
    }
    this.options.onListeningChange(false);
  }

  private stopTracks(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }

  private cleanup(): void {
    if (this.finalTimer) {
      clearTimeout(this.finalTimer);
      this.finalTimer = null;
    }
    this.stopAudioGraph();
    this.socket = null;
  }
}
