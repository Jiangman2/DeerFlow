import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, renderHook } from "@testing-library/react";
import { StrictMode, type PropsWithChildren } from "react";

import { useSmoothStreamingText } from "@/core/streamdown/use-smooth-streaming-text";

type Input = { content: string; isStreaming: boolean; streamingKey?: string };
let now = 0;
let hidden = false;
let nextTimer = 0;
const timers = new Map<number, { run: () => void; at: number }>();
const motionEvents = new EventTarget();
const motion = {
  matches: false,
  addEventListener: motionEvents.addEventListener.bind(motionEvents),
  removeEventListener: motionEvents.removeEventListener.bind(motionEvents),
};

beforeEach(() => {
  now = 0;
  hidden = false;
  nextTimer = 0;
  motion.matches = false;
  timers.clear();
  rs.spyOn(performance, "now").mockImplementation(() => now);
  rs.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
  rs.spyOn(window, "matchMedia").mockReturnValue(motion as MediaQueryList);
  const setBrowserTimeout = (
    callback: TimerHandler,
    delay?: number,
  ): number => {
    const id = ++nextTimer;
    timers.set(id, { run: callback as () => void, at: now + (delay ?? 0) });
    return id;
  };
  // The application calls the browser API; Node's ambient timer overload is
  // also present in this mixed-environment test project's TypeScript globals.
  rs.spyOn(window, "setTimeout").mockImplementation(
    setBrowserTimeout as unknown as typeof window.setTimeout,
  );
  rs.spyOn(window, "clearTimeout").mockImplementation((id) => {
    if (typeof id === "number") timers.delete(id);
  });
});

afterEach(() => {
  cleanup();
  timers.clear();
  rs.restoreAllMocks();
});

function advance(to: number) {
  act(() => {
    while (true) {
      const due = [...timers]
        .filter(([, timer]) => timer.at <= to)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at;
      timers.delete(due[0]);
      due[1].run();
    }
    now = to;
  });
}

function mount(initial: Partial<Input> = {}) {
  const input: Input = {
    content: "开始",
    isStreaming: true,
    streamingKey: "thread-a/message-a",
    ...initial,
  };
  const observations: { key?: string; text: string }[] = [];
  const hook = renderHook(
    (props: Input) => {
      const text = useSmoothStreamingText(
        props.content,
        props.isStreaming,
        props.streamingKey,
      );
      observations.push({ key: props.streamingKey, text });
      return text;
    },
    {
      initialProps: input,
      wrapper: ({ children }: PropsWithChildren) => (
        <StrictMode>{children}</StrictMode>
      ),
    },
  );
  return {
    ...hook,
    observations,
    update: async (patch: Partial<Input>) => {
      Object.assign(input, patch);
      await act(async () => hook.rerender({ ...input }));
    },
  };
}

describe("smooth live text lifecycle", () => {
  it("shows existing long history without replay on mount", () => {
    const content = "已有回答".repeat(100);
    const hook = mount({ content });
    expect(hook.result.current).toBe(content);
    expect(timers.size).toBe(0);
  });

  it("smooths a ten-character Chinese batch instead of revealing all at once", async () => {
    const hook = mount();
    const target = "开始一二三四五六七八九十";
    await hook.update({ content: target });
    expect(hook.result.current).toBe("开始");
    advance(50);
    expect(hook.result.current.length).toBeGreaterThan(2);
    expect(hook.result.current.length).toBeLessThan(target.length);
    advance(300);
    expect(hook.result.current).toBe(target);
    expect(timers.size).toBe(0);
  });

  it("continuous arrivals do not restart a pending frame or extend the deadline", async () => {
    const hook = mount();
    let target = "开始一二三四五六七八九十";
    await hook.update({ content: target });
    const scheduled = [...timers.keys()][0];
    for (let time = 5; time <= 45; time += 5) {
      now = time;
      target += "后续";
      await hook.update({ content: target });
      expect([...timers.keys()][0]).toBe(scheduled);
    }
    advance(300);
    expect(hook.result.current).toBe(target);
  });

  it("completion and cancellation synchronously show the entire received tail", async () => {
    const hook = mount();
    const target = "开始一二三四五六七八九十";
    await hook.update({ content: target });
    advance(50);
    const beforeStop = hook.observations.length;
    await hook.update({ isStreaming: false });
    expect(
      hook.observations.slice(beforeStop).every((row) => row.text === target),
    ).toBe(true);
    expect(hook.result.current).toBe(target);
    expect(timers.size).toBe(0);
  });

  it("never renders a deferred old snapshot when switching message/thread keys", async () => {
    const hook = mount();
    await hook.update({ content: "开始旧会话尚未播完的回复" });
    const next = "开始新会话的完整回复";
    await hook.update({ streamingKey: "thread-b/message-b", content: next });
    expect(hook.result.current).toBe(next);
    expect(
      hook.observations
        .filter((row) => row.key === "thread-b/message-b")
        .every((row) => row.text === next),
    ).toBe(true);
    expect(timers.size).toBe(0);
  });

  it("replaces retracted/non-append text without retaining an old tail", async () => {
    const hook = mount();
    await hook.update({ content: "开始一二三四五六七八九十" });
    await hook.update({ content: "完全替换回答" });
    expect(hook.result.current).toBe("完全替换回答");
    expect(timers.size).toBe(0);
  });

  it("flushes hidden tabs and does not replay already received text on return", async () => {
    const hook = mount();
    const target = "开始一二三四五六七八九十";
    await hook.update({ content: target });
    hidden = true;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(hook.result.current).toBe(target);
    expect(timers.size).toBe(0);
    await hook.update({ content: target + "后台收到" });
    hidden = false;
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(hook.result.current).toBe(target + "后台收到");
    expect(timers.size).toBe(0);
  });

  it("reacts to reduced-motion changes while streaming", async () => {
    const hook = mount();
    const target = "开始一二三四五六七八九十";
    await hook.update({ content: target });
    motion.matches = true;
    act(() => {
      motionEvents.dispatchEvent(new Event("change"));
    });
    expect(hook.result.current).toBe(target);
    expect(timers.size).toBe(0);
  });

  it("cleans timers on unmount even under React Strict Mode", async () => {
    const hook = mount();
    await hook.update({ content: "开始一二三四五六七八九十" });
    expect(timers.size).toBe(1);
    hook.unmount();
    expect(timers.size).toBe(0);
    advance(1000);
  });

  it("never animates tool/unidentified text", async () => {
    const hook = mount({ streamingKey: undefined });
    await hook.update({ content: "工具状态或文件已经就绪" });
    expect(hook.result.current).toBe("工具状态或文件已经就绪");
    expect(timers.size).toBe(0);
  });
});
