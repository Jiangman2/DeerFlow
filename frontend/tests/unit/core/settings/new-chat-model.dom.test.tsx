import { afterEach, beforeEach, describe, expect, it } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useThreadSettings } from "@/core/settings/hooks";
import { getThreadModelName } from "@/core/settings/local";
import {
  updateLocalSettings,
  updateThreadSettings,
} from "@/core/settings/store";

const V41 = "deepseek-v4.1-flash-1017";
const V4 = "deepseek-v4-flash";
let sequence = 0;
const nextId = () => `new-chat-model-test-${++sequence}`;

beforeEach(() => {
  updateLocalSettings("context", { model_name: V4, mode: "pro" });
});
afterEach(cleanup);

describe("ordinary new-chat model initialization", () => {
  it("uses V4.1 on the first render despite a saved V4 preference", () => {
    const seen: Array<string | undefined> = [];
    const id = nextId();
    const { result } = renderHook(() => {
      const settings = useThreadSettings(id, V41);
      seen.push(settings[0].context.model_name);
      return settings;
    });
    expect(seen[0]).toBe(V41);
    expect(result.current[0].context.mode).toBe("pro");
    expect(getThreadModelName(id)).toBe(V41);
  });

  it("keeps an explicit selection on rerender and after the thread is created", () => {
    const id = nextId();
    const { result, rerender } = renderHook(
      ({ fresh }) => useThreadSettings(id, fresh ? V41 : undefined),
      { initialProps: { fresh: true } },
    );
    act(() => result.current[1]("context", { model_name: V4 }));
    rerender({ fresh: true });
    expect(result.current[0].context.model_name).toBe(V4);
    rerender({ fresh: false });
    expect(result.current[0].context.model_name).toBe(V4);
    expect(getThreadModelName(id)).toBe(V4);
  });

  it("starts the next new thread on V4.1 after choosing V4", () => {
    const { result, rerender } = renderHook(
      ({ id }) => useThreadSettings(id, V41),
      { initialProps: { id: nextId() } },
    );
    act(() => result.current[1]("context", { model_name: V4 }));
    rerender({ id: nextId() });
    expect(result.current[0].context.model_name).toBe(V41);
  });

  it("retains the default through creation and a later remount", () => {
    const id = nextId();
    const fresh = renderHook(() => useThreadSettings(id, V41));
    fresh.unmount();
    const existing = renderHook(() => useThreadSettings(id));
    expect(existing.result.current[0].context.model_name).toBe(V41);
  });

  it("preserves existing V4 chats and does not rewrite global preferences", () => {
    const oldId = nextId();
    updateThreadSettings(oldId, "context", { model_name: V4 });
    const freshId = nextId();
    renderHook(() => useThreadSettings(freshId, V41));
    const existing = renderHook(() => useThreadSettings(oldId));
    const otherSurface = renderHook(() =>
      useThreadSettings("legacy-no-override"),
    );
    expect(existing.result.current[0].context.model_name).toBe(V4);
    expect(otherSurface.result.current[0].context.model_name).toBe(V4);
  });

  it("does not replace a fallback selection when V4.1 is unavailable", () => {
    const id = nextId();
    const { result, rerender } = renderHook(
      ({ id }) => useThreadSettings(id, V41),
      { initialProps: { id } },
    );
    act(() => result.current[1]("context", { model_name: "available-model" }));
    rerender({ id });
    expect(result.current[0].context.model_name).toBe("available-model");
  });
});
