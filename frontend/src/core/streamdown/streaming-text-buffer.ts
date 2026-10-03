/** Presentation only: never feeds shortened text back into SDK/state/storage. */
export const STREAMING_TEXT_FRAME_MS = 50;
const MAX_REVEAL_DELAY_MS = 300;
const MAX_ANIMATED_SUFFIX = 256;

const graphemeSegmenter =
  typeof Intl.Segmenter === "function"
    ? new Intl.Segmenter(undefined, { granularity: "grapheme" })
    : undefined;

/** Move forwards to a whole character, including emoji/combining sequences. */
function textBoundary(text: string, start: number, minimumEnd: number): number {
  const suffix = text.slice(start);
  if (graphemeSegmenter) {
    for (const part of graphemeSegmenter.segment(suffix)) {
      const end = start + part.index + part.segment.length;
      if (end >= minimumEnd) return end;
    }
  } else {
    let end = start;
    for (const character of suffix) {
      end += character.length;
      if (end >= minimumEnd) return end;
    }
  }
  return text.length;
}

export class StreamingTextBuffer {
  private key: string | undefined;
  private target: string;
  private visibleLength: number;
  private deadline = 0;

  constructor(text: string, key?: string) {
    // Mount/reconnect/history hydration must not replay an existing answer.
    this.key = key;
    this.target = text;
    this.visibleLength = text.length;
  }

  get text(): string {
    return this.target.slice(0, this.visibleLength);
  }

  get pending(): boolean {
    return this.visibleLength < this.target.length;
  }

  update(text: string, key: string | undefined, enabled: boolean, now: number) {
    if (key !== this.key || !enabled || !text.startsWith(this.target)) {
      this.key = key;
      this.target = text;
      this.visibleLength = text.length;
      this.deadline = 0;
      return;
    }
    if (text === this.target) return;

    // Incoming chunks do not move an existing deadline or build an endless tail.
    if (!this.pending) this.deadline = now + MAX_REVEAL_DELAY_MS;
    this.target = text;
    if (text.length - this.visibleLength > MAX_ANIMATED_SUFFIX) {
      this.visibleLength = textBoundary(
        text,
        this.visibleLength,
        text.length - MAX_ANIMATED_SUFFIX,
      );
    }
    if (this.visibleLength === 0 && text) {
      this.visibleLength = textBoundary(text, 0, 1);
    }
  }

  advance(now: number) {
    if (!this.pending) return;
    if (now >= this.deadline) {
      this.visibleLength = this.target.length;
      return;
    }
    const framesLeft = Math.max(
      1,
      Math.ceil((this.deadline - now) / STREAMING_TEXT_FRAME_MS),
    );
    const step = Math.ceil(
      (this.target.length - this.visibleLength) / framesLeft,
    );
    this.visibleLength = textBoundary(
      this.target,
      this.visibleLength,
      this.visibleLength + step,
    );
  }
}
