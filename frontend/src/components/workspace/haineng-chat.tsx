"use client";

import type { Message as SDKMessage } from "@langchain/langgraph-sdk";
import Image from "next/image";
import { useEffect, useRef, useState, type ComponentProps } from "react";

import {
  PromptInputProvider,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { InputBox } from "@/components/workspace/input-box";
import { MessageListItem } from "@/components/workspace/messages/message-list-item";
import { Welcome } from "@/components/workspace/welcome";
import { useHainengHistory } from "@/core/haineng/history";
import { consumeHainengStream } from "@/core/haineng/stream";
import { cn } from "@/lib/utils";

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  think: string;
};

export function HainengChat({ preview = false }: { preview?: boolean }) {
  const history = useHainengHistory();
  if (!history.ready) return null;
  const record = history.records.find((r) => r.id === history.activeId);
  return (
    <ArtifactsProvider scopeKey="haineng">
      <PromptInputProvider
        key={history.activeId}
        initialInput={record?.draft ?? ""}
      >
        <HainengChatContent key={history.activeId} preview={preview} />
      </PromptInputProvider>
    </ArtifactsProvider>
  );
}
function HainengChatContent({ preview }: { preview: boolean }) {
  const { textInput } = usePromptInputController();
  const history = useHainengHistory();
  const initialRecord = useRef(
    history.records.find((r) => r.id === history.activeId),
  );
  const [context, setContext] = useState<
    ComponentProps<typeof InputBox>["context"]
  >({ mode: "flash", model_name: undefined });
  const [messages, setMessages] = useState<Message[]>(
    initialRecord.current?.messages ?? [],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const session = useRef<string | undefined>(initialRecord.current?.sessionId);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [messages]);

  const saveHistory = history.save;
  const setHistoryBusy = history.setBusy;
  const recordId = history.activeId;
  const recordTime = useRef(initialRecord.current?.updatedAt ?? Date.now());
  useEffect(() => {
    setHistoryBusy(busy);
    return () => setHistoryBusy(false);
  }, [busy, setHistoryBusy]);
  useEffect(() => {
    if (busy || (messages.length === 0 && !textInput.value)) return;
    saveHistory({
      id: recordId,
      title:
        messages.find((m) => m.role === "user")?.text.slice(0, 40) ??
        "新建对话",
      messages,
      sessionId: session.current,
      draft: textInput.value,
      updatedAt: recordTime.current,
    });
  }, [messages, busy, textInput.value, saveHistory, recordId]);

  async function send(content: string) {
    if (controller.current || !content.trim()) return;
    content = content.trim();
    recordTime.current = Date.now();
    const id = crypto.randomUUID();
    const abort = new AbortController();
    controller.current = abort;
    setBusy(true);
    setError("");
    setMessages((old) => [
      ...old,
      { id: crypto.randomUUID(), role: "user", text: content, think: "" },
      { id, role: "assistant", text: "", think: "" },
    ]);
    try {
      const response = preview
        ? new Response(
            new ReadableStream<Uint8Array>({
              async start(stream) {
                const encoder = new TextEncoder();
                for (const text of [
                  "这是项目内的前端预览。",
                  "\n\n输入框、消息气泡和 Markdown 渲染复用 DeerFlow 原组件。",
                  "\n\n**没有调用任何海能服务。**",
                ]) {
                  if (abort.signal.aborted) {
                    stream.error(new Error("已停止"));
                    return;
                  }
                  stream.enqueue(
                    encoder.encode(
                      `data:${JSON.stringify({ header: { code: 0 }, payload: { sessionId: "preview-session", data: { choices: { text } } } })}\n\n`,
                    ),
                  );
                  await new Promise((resolve) => setTimeout(resolve, 400));
                }
                stream.enqueue(
                  encoder.encode('data:{"header":{"code":10000}}\n\n'),
                );
                stream.close();
              },
            }),
          )
        : await fetch("/api/haineng/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            credentials: "same-origin",
            signal: abort.signal,
            body: JSON.stringify({ content, sessionId: session.current }),
          });
      if (!response.ok) {
        const problem = (await response.json()) as { error?: string };
        throw new Error(problem.error ?? `请求失败（${response.status}）`);
      }
      if (!response.body) throw new Error("回答流为空");
      await consumeHainengStream(response.body, (event) => {
        const data = event.payload?.data;
        const choices = data?.choices;
        session.current =
          choices?.sessionId ??
          event.payload?.sessionId ??
          data?.sessionId ??
          session.current;
        if (event.header?.code === 5003) {
          setMessages((old) =>
            old.map((m) =>
              m.id === id
                ? {
                    ...m,
                    text: event.header?.message ?? "内容被拦截",
                    think: "",
                  }
                : m,
            ),
          );
        } else if (event.header?.code === 0 || event.header?.code === 2) {
          setMessages((old) =>
            old.map((m) =>
              m.id === id
                ? {
                    ...m,
                    text: m.text + (choices?.text ?? ""),
                    think: m.think + (choices?.think ?? ""),
                  }
                : m,
            ),
          );
        }
      });
    } catch (cause) {
      setError(
        abort.signal.aborted
          ? "已停止接收，已保留收到的内容。"
          : cause instanceof Error
            ? cause.message
            : "请求失败",
      );
    } finally {
      controller.current = null;
      setBusy(false);
    }
  }

  const welcome = messages.length === 0;
  const sdkMessages = messages.map(
    (m): SDKMessage => ({
      id: m.id,
      type: m.role === "user" ? "human" : "ai",
      content: m.text,
      ...(m.think ? { additional_kwargs: { reasoning_content: m.think } } : {}),
    }),
  );
  return (
    <div
      className="relative flex size-full min-h-0 justify-between"
      data-testid="haineng-chat"
    >
      <header
        className={cn(
          "cnooc-chat-header absolute top-0 right-0 left-0 z-40 flex h-12 items-center gap-2 px-4",
          welcome && "cnooc-chat-header-welcome",
        )}
      >
        {welcome && (
          <div className="cnooc-desktop-brandline">
            <Image
              alt=""
              aria-hidden="true"
              className="cnooc-brandline-mark"
              height={128}
              width={128}
              src="/images/cnooc-haineng-mark.png"
              unoptimized
            />
            <strong>智启海油&nbsp; 赋能未来</strong>
            <span aria-hidden="true">|</span>
            <span>海能 AI</span>
          </div>
        )}
        <div className="flex-1" />
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            history.create();
          }}
        >
          新建对话
        </Button>
      </header>
      <main
        className={cn(
          "cnooc-chat-main flex min-h-0 max-w-full grow flex-col",
          !welcome && "cnooc-chat-main-task",
        )}
      >
        {welcome && (
          <div className="cnooc-welcome-stage">
            <Welcome mode="flash" />
          </div>
        )}
        <div className="flex min-h-0 flex-1 justify-center overflow-y-auto">
          <div className="w-full max-w-(--container-width-md) space-y-8 px-4 pt-14 pb-8">
            {sdkMessages.map((message, index) => (
              <MessageListItem
                key={message.id}
                message={message}
                threadId="haineng"
                isLoading={busy && index === sdkMessages.length - 1}
                showCopyButton={!busy}
              />
            ))}
            <div ref={bottom} />
          </div>
        </div>
        <div
          className={cn(
            "cnooc-composer-dock right-0 bottom-0 left-0 z-30 flex min-w-0 justify-center px-2 sm:px-4",
            welcome
              ? "cnooc-composer-dock-welcome app-safe-area-bottom absolute pb-3 sm:pb-0"
              : "relative shrink-0 pb-4",
          )}
        >
          <div
            className={cn(
              "cnooc-composer-frame relative w-full",
              welcome
                ? "max-w-(--container-width-sm)"
                : "max-w-(--container-width-md)",
            )}
          >
            {error && (
              <p role="alert" className="text-destructive mb-2 text-sm">
                {error}
              </p>
            )}
            <InputBox
              className={cn(
                "bg-background/5 w-full",
                welcome && "cnooc-welcome-composer",
              )}
              isWelcomeMode={welcome}
              threadId="haineng"
              externalMessages={sdkMessages}
              textOnly
              context={context}
              onContextChange={setContext}
              status={busy ? "streaming" : "ready"}
              onSubmit={(message) => send(message.text)}
              onStop={() => controller.current?.abort()}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
