"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { uuid } from "@/core/utils/uuid";

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();

  const searchParams = useSearchParams();
  const newChatNonce = searchParams.get("ts") ?? "";

  const [threadId, setThreadId] = useState(() =>
    threadIdFromPath === "new" ? uuid() : threadIdFromPath,
  );
  const [isNewThread, setIsNewThread] = useState(
    () => threadIdFromPath === "new",
  );

  const lastRouteKeyRef = useRef<string | null>(null);
  useEffect(() => {
    const routeKey =
      threadIdFromPath === "new" ? `new:${newChatNonce}` : threadIdFromPath;
    if (lastRouteKeyRef.current === routeKey) {
      return;
    }
    lastRouteKeyRef.current = routeKey;

    if (threadIdFromPath === "new") {
      setIsNewThread(true);
      setThreadId(uuid());
      return;
    }

    setIsNewThread(false);
    setThreadId(threadIdFromPath);
  }, [newChatNonce, threadIdFromPath]);

  const isMock = searchParams.get("mock") === "true";
  return { threadId, isNewThread, setThreadId, setIsNewThread, isMock };
}
