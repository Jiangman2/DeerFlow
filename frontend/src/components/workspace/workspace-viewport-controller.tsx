"use client";

import { useEffect } from "react";

const MOBILE_VIEWPORT_QUERY =
  "(max-width: 47.999rem), (orientation: landscape) and (max-height: 32rem) and (max-width: 64rem)";
const COMPOSER_EDITABLE_SELECTOR =
  '.cnooc-composer textarea, .cnooc-composer input, .cnooc-composer [contenteditable="true"]';

/**
 * Keeps the workspace bound to the actually visible mobile viewport.
 *
 * `dvh` and `interactive-widget` cover modern browsers, while VisualViewport
 * closes the remaining Android WebView and iOS Safari gaps during keyboard,
 * browser-chrome, orientation, and pinch-zoom changes.
 */
export function WorkspaceViewportController() {
  useEffect(() => {
    const shell = document.querySelector<HTMLElement>(".cnooc-workspace-shell");
    if (!shell) return;

    const mobileQuery = window.matchMedia(MOBILE_VIEWPORT_QUERY);
    const visualViewport = window.visualViewport;
    let baselineHeight = 0;
    let frameId = 0;
    let orientationTimer = 0;

    const clearMobileState = () => {
      shell.style.removeProperty("--app-viewport-height");
      delete shell.dataset.mobileComposerFocused;
      delete shell.dataset.mobileKeyboardOpen;
      baselineHeight = 0;
    };

    const updateViewport = () => {
      frameId = 0;
      if (!mobileQuery.matches) {
        clearMobileState();
        return;
      }

      const visibleHeight = Math.max(
        1,
        Math.round(visualViewport?.height ?? window.innerHeight),
      );
      const activeElement = document.activeElement;
      const composerFocused =
        activeElement instanceof Element &&
        activeElement.matches(COMPOSER_EDITABLE_SELECTOR);

      if (!composerFocused) {
        baselineHeight = Math.max(baselineHeight, visibleHeight);
      } else if (baselineHeight === 0) {
        baselineHeight = Math.max(window.innerHeight, visibleHeight);
      }

      const keyboardThreshold = Math.max(96, baselineHeight * 0.15);
      const keyboardOpen =
        composerFocused &&
        (baselineHeight - visibleHeight >= keyboardThreshold ||
          (visualViewport?.scale ?? 1) > 1.05);

      shell.style.setProperty("--app-viewport-height", `${visibleHeight}px`);
      shell.dataset.mobileComposerFocused = String(composerFocused);
      shell.dataset.mobileKeyboardOpen = String(keyboardOpen);
    };

    const scheduleUpdate = () => {
      if (frameId) return;
      frameId = window.requestAnimationFrame(updateViewport);
    };

    const handleFocusOut = () => {
      window.setTimeout(() => {
        scheduleUpdate();
        if (!document.activeElement?.matches(COMPOSER_EDITABLE_SELECTOR)) {
          window.scrollTo(0, 0);
        }
      }, 0);
    };

    const handleOrientationChange = () => {
      baselineHeight = 0;
      window.clearTimeout(orientationTimer);
      orientationTimer = window.setTimeout(scheduleUpdate, 180);
      scheduleUpdate();
    };

    scheduleUpdate();
    window.addEventListener("resize", scheduleUpdate, { passive: true });
    window.addEventListener("orientationchange", handleOrientationChange, {
      passive: true,
    });
    window.addEventListener("pageshow", scheduleUpdate, { passive: true });
    document.addEventListener("focusin", scheduleUpdate, { passive: true });
    document.addEventListener("focusout", handleFocusOut, { passive: true });
    document.addEventListener("visibilitychange", scheduleUpdate, {
      passive: true,
    });
    mobileQuery.addEventListener("change", scheduleUpdate);
    visualViewport?.addEventListener("resize", scheduleUpdate, {
      passive: true,
    });
    visualViewport?.addEventListener("scroll", scheduleUpdate, {
      passive: true,
    });

    return () => {
      if (frameId) window.cancelAnimationFrame(frameId);
      window.clearTimeout(orientationTimer);
      window.removeEventListener("resize", scheduleUpdate);
      window.removeEventListener("orientationchange", handleOrientationChange);
      window.removeEventListener("pageshow", scheduleUpdate);
      document.removeEventListener("focusin", scheduleUpdate);
      document.removeEventListener("focusout", handleFocusOut);
      document.removeEventListener("visibilitychange", scheduleUpdate);
      mobileQuery.removeEventListener("change", scheduleUpdate);
      visualViewport?.removeEventListener("resize", scheduleUpdate);
      visualViewport?.removeEventListener("scroll", scheduleUpdate);
      clearMobileState();
    };
  }, []);

  return null;
}
