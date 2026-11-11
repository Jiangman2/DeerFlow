"use client";

import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
} from "@tanstack/react-virtual";
import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  type Key,
  type ReactNode,
} from "react";
import { useStickToBottomContext } from "use-stick-to-bottom";

import type { MessageGroup } from "@/core/messages/utils";

const VIRTUALIZATION_THRESHOLD = 60;
const ESTIMATED_ROW_HEIGHT = 176;

function groupKey(group: MessageGroup | undefined, index: number) {
  return (
    group?.id ??
    group?.messages.find((message) => message.id)?.id ??
    `${group?.type ?? "message"}:${index}`
  );
}

export function VirtualMessageList({
  groups,
  isLoading,
  renderGroup,
}: {
  groups: readonly MessageGroup[];
  isLoading: boolean;
  renderGroup: (group: MessageGroup, index: number) => ReactNode;
}) {
  const { isAtBottom, scrollRef, scrollToBottom, state, stopScroll } =
    useStickToBottomContext();
  const activeIndex = isLoading ? groups.length - 1 : -1;
  const getItemKey = useCallback(
    (index: number) => groupKey(groups[index], index),
    [groups],
  );
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indices = defaultRangeExtractor(range);
      if (activeIndex >= 0 && !indices.includes(activeIndex)) {
        indices.push(activeIndex);
        indices.sort((a, b) => a - b);
      }
      return indices;
    },
    [activeIndex],
  );
  const virtualizer = useVirtualizer({
    count: groups.length,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    getItemKey,
    getScrollElement: () => scrollRef.current,
    overscan: 8,
    rangeExtractor,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const shouldVirtualize = groups.length >= VIRTUALIZATION_THRESHOLD;
  const positionedInitialVirtualWindowRef = useRef(false);
  const previousCountRef = useRef(groups.length);
  const previousFirstKeyRef = useRef<string | undefined>(undefined);
  const anchorRef = useRef<{ key: Key; viewportOffset: number } | undefined>(
    undefined,
  );

  useLayoutEffect(() => {
    if (!shouldVirtualize) {
      positionedInitialVirtualWindowRef.current = false;
      return;
    }
    if (positionedInitialVirtualWindowRef.current) return;

    let frame = 0;
    let attempts = 0;
    let stableFrames = 0;
    let previousHeight = -1;
    let cancelled = false;
    let element: HTMLElement | null = null;
    let detachElementListeners: (() => void) | undefined;
    let finishRestoration: () => void = () => undefined;
    const restorationFinished = new Promise<void>((resolve) => {
      finishRestoration = resolve;
    });
    const cancelRestoration = () => {
      cancelled = true;
      positionedInitialVirtualWindowRef.current = true;
      cancelAnimationFrame(frame);
      finishRestoration();
      stopScroll();
    };
    const restoreBottom = () => {
      if (cancelled || !element) return;
      // scrollToIndex uses estimates for rows outside the virtual window.
      // Anchor to the real scroll extent while the final rows are measured.
      state.scrollTop = Math.max(
        0,
        element.scrollHeight - element.clientHeight,
      );
      const height = element.scrollHeight;
      const lastRow = virtualizer.getVirtualItems().at(-1);
      stableFrames =
        height === previousHeight && lastRow?.index === groups.length - 1
          ? stableFrames + 1
          : 0;
      previousHeight = height;
      attempts += 1;
      if (stableFrames >= 8 || attempts >= 120) {
        positionedInitialVirtualWindowRef.current = true;
        finishRestoration();
        return;
      }
      frame = requestAnimationFrame(restoreBottom);
    };
    const startRestoration = (scrollElement: HTMLElement) => {
      element = scrollElement;
      attempts = 0;
      element.addEventListener("wheel", cancelRestoration, { passive: true });
      element.addEventListener("touchstart", cancelRestoration, {
        passive: true,
      });
      detachElementListeners = () => {
        element?.removeEventListener("wheel", cancelRestoration);
        element?.removeEventListener("touchstart", cancelRestoration);
      };
      // Keep one scroll owner while estimated rows become measured rows. Native
      // scrollTop writes bypass the stick-to-bottom hook's resize/escape tracking.
      void scrollToBottom({
        animation: "instant",
        duration: restorationFinished,
      });
      restoreBottom();
    };
    // The stick-to-bottom scroll element is owned by an ancestor, so it can
    // attach after this child's layout effect already ran. Wait for it instead
    // of giving up on the first pass, which would leave the first virtual
    // window stranded mid-list.
    const waitForScrollElement = () => {
      if (cancelled) return;
      const scrollElement = scrollRef.current;
      if (scrollElement) {
        startRestoration(scrollElement);
        return;
      }
      if (attempts >= 60) {
        finishRestoration();
        return;
      }
      attempts += 1;
      frame = requestAnimationFrame(waitForScrollElement);
    };
    waitForScrollElement();
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      finishRestoration();
      detachElementListeners?.();
    };
  }, [
    groups.length,
    scrollRef,
    scrollToBottom,
    shouldVirtualize,
    state,
    stopScroll,
    virtualizer,
  ]);

  useLayoutEffect(() => {
    if (groups.length > previousCountRef.current && isAtBottom) {
      void scrollToBottom({
        animation: "instant",
        preserveScrollPosition: true,
      });
    }
    previousCountRef.current = groups.length;
  }, [groups.length, isAtBottom, scrollToBottom]);

  useLayoutEffect(() => {
    const firstKey = getItemKey(0);
    const previousFirstKey = previousFirstKeyRef.current;
    const anchor = anchorRef.current;
    if (
      previousFirstKey !== undefined &&
      firstKey !== previousFirstKey &&
      anchor
    ) {
      const anchorIndex = groups.findIndex(
        (group, index) => groupKey(group, index) === anchor.key,
      );
      if (anchorIndex >= 0) {
        const anchorStart = virtualizer.getOffsetForIndex(
          anchorIndex,
          "start",
        )?.[0];
        if (anchorStart !== undefined) {
          virtualizer.scrollToOffset(anchorStart - anchor.viewportOffset, {
            behavior: "auto",
          });
        }
      }
    }
    previousFirstKeyRef.current = firstKey;

    const scrollOffset = virtualizer.scrollOffset ?? 0;
    const firstVisible = virtualItems.find((item) => item.end >= scrollOffset);
    if (firstVisible) {
      anchorRef.current = {
        key: firstVisible.key,
        viewportOffset: firstVisible.start - scrollOffset,
      };
    }
  }, [getItemKey, groups, virtualItems, virtualizer]);

  const renderedAll = useMemo(
    () =>
      shouldVirtualize
        ? null
        : groups.map((group, index) => (
            <div key={getItemKey(index)}>{renderGroup(group, index)}</div>
          )),
    [getItemKey, groups, renderGroup, shouldVirtualize],
  );

  if (!shouldVirtualize) {
    return <div className="flex flex-col gap-8">{renderedAll}</div>;
  }

  return (
    <div
      className="relative w-full"
      style={{ height: `${virtualizer.getTotalSize()}px` }}
    >
      {virtualItems.map((virtualRow) => {
        const group = groups[virtualRow.index];
        if (!group) return null;
        return (
          <div
            key={virtualRow.key}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            className="absolute top-0 left-0 w-full pb-8"
            style={{ transform: `translateY(${virtualRow.start}px)` }}
          >
            {renderGroup(group, virtualRow.index)}
          </div>
        );
      })}
    </div>
  );
}
