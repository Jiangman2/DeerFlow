import { describe, expect, it } from "@rstest/core";

import {
  STREAMING_TEXT_FRAME_MS,
  StreamingTextBuffer,
} from "@/core/streamdown/streaming-text-buffer";

describe("browser-only streaming text smoothing", () => {
  it("shows hydrated/reconnected text immediately without replaying history", () => {
    const content = "已有回答".repeat(1000);
    const buffer = new StreamingTextBuffer(content, "thread/message");
    buffer.update(content, "thread/message", true, 0);
    expect(buffer.text).toBe(content);
    expect(buffer.pending).toBe(false);
  });

  it("reveals an appended Chinese batch over multiple display frames", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    const target = "开始一二三四五六七八九十";
    buffer.update(target, "a", true, 0);
    expect(buffer.text).toBe("开始");
    buffer.advance(STREAMING_TEXT_FRAME_MS);
    expect(buffer.text.length).toBeGreaterThan(2);
    expect(buffer.text.length).toBeLessThan(target.length);
    buffer.advance(320);
    expect(buffer.text).toBe(target);
    expect(buffer.pending).toBe(false);
  });

  it("shows the first new character immediately", () => {
    const buffer = new StreamingTextBuffer("", "a");
    buffer.update("你好世界", "a", true, 0);
    expect(buffer.text).toBe("你");
    buffer.advance(320);
    expect(buffer.text).toBe("你好世界");
  });

  it("does not extend the deadline when more chunks arrive", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    buffer.update("开始一二三四五六七八九十", "a", true, 0);
    buffer.advance(40);
    const prefix = buffer.text;
    const target = "开始一二三四五六七八九十还有后续内容";
    buffer.update(target, "a", true, 300);
    expect(buffer.text.startsWith(prefix)).toBe(true);
    buffer.advance(320);
    expect(buffer.text).toBe(target);
  });

  it("ignores repeated/replayed snapshots without duplicating text", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    const target = "开始一二三四五六七八九十";
    buffer.update(target, "a", true, 0);
    buffer.advance(40);
    const prefix = buffer.text;
    buffer.update(target, "a", true, 300);
    expect(buffer.text).toBe(prefix);
    buffer.advance(320);
    expect(buffer.text).toBe(target);
  });

  it("bounds large bursts instead of playing thousands of characters", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    const target = "开始" + "中文".repeat(5000);
    buffer.update(target, "a", true, 0);
    expect(target.length - buffer.text.length).toBeLessThanOrEqual(256);
    buffer.advance(320);
    expect(buffer.text).toBe(target);
  });

  it("flushes all received text after a delayed browser frame", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    const target = "开始一二三四五六七八九十";
    buffer.update(target, "a", true, 0);
    buffer.advance(5000);
    expect(buffer.text).toBe(target);
    expect(buffer.pending).toBe(false);
  });

  it("flushes completion, cancellation and disabled animation immediately", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    const target = "开始一二三四五六七八九十";
    buffer.update(target, "a", true, 0);
    buffer.advance(40);
    buffer.update(target, "a", false, 41);
    expect(buffer.text).toBe(target);
    expect(buffer.pending).toBe(false);
  });

  it("replaces edited/retracted content without retaining an old tail", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    buffer.update("开始一二三四五六七八九十", "a", true, 0);
    buffer.advance(40);
    buffer.update("开始新的答案", "a", true, 80);
    expect(buffer.text).toBe("开始新的答案");
    expect(buffer.pending).toBe(false);
    buffer.update("开始", "a", true, 90);
    expect(buffer.text).toBe("开始");
  });

  it("isolates a new message even when it shares an old text prefix", () => {
    const buffer = new StreamingTextBuffer("相同开头", "thread-a/message-a");
    buffer.update("相同开头之前的回答", "thread-a/message-a", true, 0);
    buffer.update("相同开头新的回答", "thread-b/message-b", true, 40);
    expect(buffer.text).toBe("相同开头新的回答");
    buffer.advance(80);
    expect(buffer.text).toBe("相同开头新的回答");
  });

  it("starts a fresh short window after catching up", () => {
    const buffer = new StreamingTextBuffer("开始", "a");
    buffer.update("开始第一批", "a", true, 0);
    buffer.advance(320);
    buffer.update("开始第一批第二批一二三四五六", "a", true, 1000);
    expect(buffer.pending).toBe(true);
    buffer.advance(1320);
    expect(buffer.text).toBe("开始第一批第二批一二三四五六");
  });

  it("does not split Chinese, emoji, ZWJ or combining graphemes", () => {
    const buffer = new StreamingTextBuffer("", "a");
    const target = "👩‍💻e\u0301中国🇨🇳🛢️完成";
    const prefixes = new Set([""]);
    let prefix = "";
    for (const segment of new Intl.Segmenter(undefined, {
      granularity: "grapheme",
    }).segment(target)) {
      prefix += segment.segment;
      prefixes.add(prefix);
    }
    buffer.update(target, "a", true, 0);
    expect(prefixes.has(buffer.text)).toBe(true);
    for (let now = 40; now <= 320; now += 40) {
      buffer.advance(now);
      expect(prefixes.has(buffer.text)).toBe(true);
    }
    expect(buffer.text).toBe(target);
  });

  it("preserves every byte of Markdown fences, tables and artifact links", () => {
    const target = [
      "| 阶段 | 交付物 |",
      "| --- | --- |",
      "| 区域普查 | 地质报告 |",
      "```python",
      'print("勘探🛢️")',
      "```",
      "[交付清单](/mnt/user-data/outputs/交付清单.md)",
    ].join("\n");
    const buffer = new StreamingTextBuffer("", "a");
    for (let end = 10; end < target.length; end += 10) {
      buffer.update(target.slice(0, end), "a", true, end * 10);
      buffer.advance(end * 10 + 40);
    }
    buffer.update(target, "a", false, 2000);
    expect(buffer.text).toBe(target);
  });
});
