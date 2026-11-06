"use client";

import { useCallback, useLayoutEffect, type RefObject } from "react";

const HEIGHT_PROPERTY = "--textarea-content-height";
const MAX_ROWS = 6;

// The chat composer consumes this property instead of relying on field-sizing,
// which is not available in some enterprise browsers.
export function useTextareaAutosize(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string,
  enabled: boolean,
) {
  const resize = useCallback(() => {
    const textarea = ref.current;
    if (!textarea || textarea.clientWidth === 0) return;

    const style = getComputedStyle(textarea);
    const lineHeight = parseFloat(style.lineHeight);
    if (!Number.isFinite(lineHeight)) return;
    const padding =
      parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    const border =
      parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
    const previousScrollTop = textarea.scrollTop;
    const previousOverflowY = textarea.style.overflowY;

    // A classic scrollbar must not narrow the draft while measuring its height.
    textarea.style.overflowY = "hidden";
    // Reset before measuring so deleting text can shrink the composer too.
    textarea.style.setProperty(HEIGHT_PROPERTY, "0px");
    const height = Math.min(
      lineHeight * MAX_ROWS + padding + border,
      Math.max(lineHeight + padding + border, textarea.scrollHeight + border),
    );
    textarea.style.setProperty(HEIGHT_PROPERTY, `${height}px`);
    if (previousOverflowY) textarea.style.overflowY = previousOverflowY;
    else textarea.style.removeProperty("overflow-y");
    textarea.scrollTop = previousScrollTop;
  }, [ref]);

  useLayoutEffect(() => {
    const textarea = ref.current;
    if (!enabled || !textarea) return;

    let width = textarea.getBoundingClientRect().width;
    const observer =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(() => {
            const nextWidth = textarea.getBoundingClientRect().width;
            // Ignore our own height writes; panel changes can rewrap the draft
            // without changing its value or resizing the browser window.
            if (nextWidth !== width) {
              width = nextWidth;
              resize();
            }
          });
    observer?.observe(textarea);
    window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      textarea.style.removeProperty(HEIGHT_PROPERTY);
    };
  }, [enabled, ref, resize]);

  useLayoutEffect(() => {
    if (enabled) resize();
  }, [enabled, resize, value]);
}
