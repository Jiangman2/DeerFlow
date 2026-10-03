"use client";

import { useParams, usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { isStaticDemoThreadId } from "@/core/threads/static-demo";
import { uuid } from "@/core/utils/uuid";

export const THREAD_CHAT_RESET_EVENT = "platform:thread-chat-reset";
export const THREAD_CHAT_ROUTE_CHANGE_EVENT = "platform:thread-chat-route-change";

type ThreadChatResetDetail = {
  deletedThreadId: string;
  nextPath: string;
  force?: boolean;
};

type ThreadChatRouteChangeDetail = {
  pathname: string;
};

export function notifyThreadChatRouteChange(pathname: string) {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<ThreadChatRouteChangeDetail>(
      THREAD_CHAT_ROUTE_CHANGE_EVENT,
      { detail: { pathname } },
    ),
  );
}

export function resetThreadChatAfterDelete(detail: ThreadChatResetDetail) {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(
    new CustomEvent<ThreadChatResetDetail>(THREAD_CHAT_RESET_EVENT, {
      detail,
    }),
  );
}

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const pathname = usePathname();
  // Keep the official Next route as the source of truth for normal navigation.
  // The one native-history transition used when a new thread is first created
  // does not notify Next's router, so keep a short-lived local override for
  // that transition instead of forcing a router/RSC remount through a live
  // LangGraph stream.
  const [nativePathname, setNativePathname] = useState<string | null>(null);
  useEffect(() => {
    setNativePathname(null);
  }, [pathname]);
  useEffect(() => {
    const handleRouteChange = (event: Event) => {
      const nextPathname = (event as CustomEvent<ThreadChatRouteChangeDetail>)
        .detail?.pathname;
      if (typeof nextPathname === "string" && nextPathname.length > 0) {
        setNativePathname(nextPathname);
      }
    };
    window.addEventListener(
      THREAD_CHAT_ROUTE_CHANGE_EVENT,
      handleRouteChange,
    );
    return () =>
      window.removeEventListener(
        THREAD_CHAT_ROUTE_CHANGE_EVENT,
        handleRouteChange,
      );
  }, []);
  // Also honor an explicit delete reset before its router transition commits.
  // A native first-run URL replacement can leave Next on /new already, making
  // that router transition a no-op; its local override must be reset as well.
  const effectivePathname = nativePathname ?? pathname;
  const pathThreadId =
    effectivePathname.split("/").filter(Boolean).at(-1) ?? threadIdFromPath;
  const isNewPath = pathThreadId === "new";
  const newThreadIdRef = useRef<string | null>(
    pathThreadId === "new" ? uuid() : null,
  );

  if (isNewPath && !newThreadIdRef.current) {
    newThreadIdRef.current = uuid();
  }

  const searchParams = useSearchParams();
  const [threadId, setThreadIdState] = useState(() => {
    return pathThreadId === "new"
      ? (newThreadIdRef.current ?? uuid())
      : pathThreadId;
  });

  const [isNewThreadState, setIsNewThreadState] = useState(
    () => pathThreadId === "new",
  );

  const resetToNewThread = useCallback(() => {
    const nextThreadId = uuid();
    newThreadIdRef.current = nextThreadId;
    setIsNewThreadState(true);
    setThreadIdState(nextThreadId);
  }, []);

  useEffect(() => {
    if (effectivePathname.endsWith("/new")) {
      const nextThreadId = newThreadIdRef.current ?? uuid();
      newThreadIdRef.current = nextThreadId;
      setIsNewThreadState(true);
      setThreadIdState(nextThreadId);
      return;
    }
    newThreadIdRef.current = null;
    setIsNewThreadState(false);
    setThreadIdState(pathThreadId);
  }, [effectivePathname, pathThreadId]);

  useEffect(() => {
    const handleReset = (event: Event) => {
      const detail = (event as CustomEvent<ThreadChatResetDetail>).detail;
      if (!detail?.nextPath) {
        return;
      }

      const currentPathname = window.location.pathname;
      const isDeletingCurrentThread =
        detail.force === true ||
        detail.deletedThreadId === threadId ||
        detail.deletedThreadId === pathThreadId ||
        currentPathname.endsWith(`/${detail.deletedThreadId}`);

      if (!isDeletingCurrentThread) {
        return;
      }

      // URL replacement is owned by the caller's Next router action; this hook
      // only resets local chat state so the router state and browser URL stay
      // in sync.
      setNativePathname(detail.nextPath);
      resetToNewThread();
    };

    window.addEventListener(THREAD_CHAT_RESET_EVENT, handleReset);
    return () =>
      window.removeEventListener(THREAD_CHAT_RESET_EVENT, handleReset);
  }, [pathThreadId, resetToNewThread, threadId]);

  const setThreadId = useCallback((nextThreadId: string) => {
    newThreadIdRef.current = null;
    setThreadIdState(nextThreadId);
  }, []);

  const setIsNewThread = useCallback((nextIsNewThread: boolean) => {
    if (!nextIsNewThread) {
      newThreadIdRef.current = null;
    }
    setIsNewThreadState(nextIsNewThread);
  }, []);

  const isMock = searchParams.get("mock") === "true";
  // Prefer the URL-derived ID during navigation. State setters run after the
  // render, so returning the state value here can keep the previous thread's
  // history/stream attached to the newly selected URL.
  const resolvedThreadId = isNewPath
    ? (newThreadIdRef.current ?? threadId)
    : pathThreadId;
  const resolvedIsNewThread =
    isNewPath || (isNewThreadState && pathThreadId === "new");
  const effectiveThreadId = resolvedIsNewThread ? null : resolvedThreadId;
  return {
    threadId: resolvedThreadId,
    setThreadId,
    isNewThread: resolvedIsNewThread,
    setIsNewThread,
    isMock,
    isStaticDemo: isStaticDemoThreadId(effectiveThreadId),
  };
}
