"use client";

import { useEffect, useRef, useState } from "react";

import { consumeHainengStream } from "@/core/haineng/stream";

type Message = { id: string; role: "user" | "assistant"; text: string; think: string };

export function HainengChat() {
  const [draft, setDraft] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const session = useRef<string | undefined>(undefined);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "nearest" }); }, [messages]);

  async function send() {
    if (controller.current || !draft.trim()) return;
    const content = draft.trim();
    const id = crypto.randomUUID();
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true); setError(""); setDraft("");
    setMessages((old) => [...old,
      { id: crypto.randomUUID(), role: "user", text: content, think: "" },
      { id, role: "assistant", text: "", think: "" },
    ]);
    try {
      const response = await fetch("/api/haineng/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin", signal: abort.signal,
        body: JSON.stringify({ content, sessionId: session.current }),
      });
      if (!response.ok) {
        const problem = await response.json() as { error?: string };
        throw new Error(problem.error ?? `请求失败（${response.status}）`);
      }
      if (!response.body) throw new Error("回答流为空");
      await consumeHainengStream(response.body, (event) => {
        const data = event.payload?.data;
        const choices = data?.choices;
        session.current = choices?.sessionId ?? event.payload?.sessionId ?? data?.sessionId ?? session.current;
        if (event.header?.code === 5003) {
          setMessages((old) => old.map((m) => m.id === id ? { ...m, text: event.header?.message ?? "内容被拦截", think: "" } : m));
        } else if (event.header?.code === 0 || event.header?.code === 2) {
          setMessages((old) => old.map((m) => m.id === id ? {
            ...m, text: m.text + (choices?.text ?? ""), think: m.think + (choices?.think ?? ""),
          } : m));
        }
      });
    } catch (cause) {
      setError(abort.signal.aborted ? "已停止接收，已保留收到的内容。" : cause instanceof Error ? cause.message : "请求失败");
    } finally {
      controller.current = null;
      setBusy(false);
    }
  }

  return (
    <section aria-label="海能 AI 对话" className="flex h-full min-h-0 w-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b px-4 py-3">
        <h2 className="font-semibold">海能 AI</h2>
        <button type="button" disabled={busy} className="rounded border px-3 py-2 disabled:opacity-50"
          onClick={() => { session.current = undefined; setMessages([]); setError(""); setDraft(""); }}>新建对话</button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {messages.length === 0 && <p className="text-muted-foreground py-12 text-center">你好，有什么可以帮你？</p>}
          {messages.map((message) => (
            <article key={message.id} aria-label={message.role === "user" ? "我的消息" : "海能回复"}
              className="rounded-xl border p-4 [overflow-wrap:anywhere]">
              <p className="text-muted-foreground mb-2 text-xs">{message.role === "user" ? "你" : "海能 AI"}</p>
              {message.think && <details><summary>思考过程</summary><p className="whitespace-pre-wrap text-sm">{message.think}</p></details>}
              <p className="whitespace-pre-wrap">{message.text || (busy && message.role === "assistant" ? "正在回答…" : "")}</p>
            </article>
          ))}
          <div ref={bottom} />
        </div>
      </div>
      <form className="mx-auto w-full max-w-3xl shrink-0 space-y-2 p-4" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        {error && <p role="alert" className="text-destructive text-sm">{error}</p>}
        <textarea aria-label="发送给海能 AI 的消息" placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          value={draft} maxLength={20000} disabled={busy}
          className="min-h-24 w-full resize-none rounded-xl border p-3 text-base"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault(); void send();
            }
          }} />
        <div className="flex justify-end gap-2">
          {busy ? <button type="button" className="rounded border px-4 py-2" onClick={() => controller.current?.abort()}>停止接收</button>
            : <button type="submit" disabled={!draft.trim()} className="bg-primary text-primary-foreground rounded px-4 py-2 disabled:opacity-50">发送</button>}
        </div>
      </form>
    </section>
  );
}
