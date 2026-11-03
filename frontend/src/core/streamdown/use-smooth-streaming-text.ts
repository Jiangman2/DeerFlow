"use client";

import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";

import {
  STREAMING_TEXT_FRAME_MS,
  StreamingTextBuffer,
} from "./streaming-text-buffer";

/** Smooth only appended live text; the original content remains authoritative. */
export function useSmoothStreamingText(
  content: string,
  isStreaming: boolean,
  streamingKey?: string,
): string {
  const [canAnimate, setCanAnimate] = useState(false);
  const [buffer] = useState(
    () => new StreamingTextBuffer(content, streamingKey),
  );
  const [snapshot, setSnapshot] = useState({
    key: streamingKey,
    target: content,
    text: content,
  });
  const applyContentRef = useRef<((text: string) => void) | null>(null);
  const enabled = isStreaming && Boolean(streamingKey) && canAnimate;
  const source = useMemo(
    () => ({ key: streamingKey, text: content }),
    [content, streamingKey],
  );
  const deferredSource = useDeferredValue(source);
  // Retain the existing low-priority stream scheduling, but never defer a
  // message switch, replacement, stop or accessibility-driven flush.
  const targetContent =
    enabled &&
    deferredSource.key === streamingKey &&
    content.startsWith(deferredSource.text)
      ? deferredSource.text
      : content;

  useEffect(() => {
    if (!isStreaming || !streamingKey) {
      setCanAnimate(false);
      return;
    }
    const motion = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const refresh = () => setCanAnimate(!motion?.matches && !document.hidden);
    refresh();
    document.addEventListener("visibilitychange", refresh);
    if (motion?.addEventListener) motion.addEventListener("change", refresh);
    else motion?.addListener(refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      if (motion?.removeEventListener)
        motion.removeEventListener("change", refresh);
      else motion?.removeListener(refresh);
    };
  }, [isStreaming, streamingKey]);

  useEffect(() => {
    let timer: number | undefined;
    let target = "";
    let disposed = false;
    const publish = () => {
      const next = { key: streamingKey, target, text: buffer.text };
      setSnapshot((previous) =>
        previous.key === next.key &&
        previous.target === next.target &&
        previous.text === next.text
          ? previous
          : next,
      );
    };
    const tick = () => {
      timer = undefined;
      if (disposed) return;
      buffer.advance(performance.now());
      publish();
      if (buffer.pending)
        timer = window.setTimeout(tick, STREAMING_TEXT_FRAME_MS);
    };
    applyContentRef.current = (text) => {
      target = text;
      buffer.update(text, streamingKey, enabled, performance.now());
      publish();
      // Keep an existing tick: fast SSE arrivals must not postpone every frame.
      if (buffer.pending && timer === undefined)
        timer = window.setTimeout(tick, STREAMING_TEXT_FRAME_MS);
      else if (!buffer.pending && timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    };
    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      applyContentRef.current = null;
    };
  }, [buffer, enabled, streamingKey]);

  useEffect(() => {
    applyContentRef.current?.(targetContent);
  }, [targetContent, enabled, streamingKey]);

  // Completion/stop, replacement and navigation are exact on their first render.
  if (
    !enabled ||
    snapshot.key !== streamingKey ||
    !content.startsWith(snapshot.target)
  ) {
    return content;
  }
  return snapshot.text;
}
