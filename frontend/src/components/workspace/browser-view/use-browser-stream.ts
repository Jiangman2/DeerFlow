"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { browserStreamURL } from "./api";
import { LatestBrowserFrameBuffer } from "./frame-buffer";

export interface BrowserTab {
  index: number;
  title: string;
  url: string;
  active: boolean;
}

export type BrowserInputEvent =
  | { type: "login_control"; action: "begin" | "resume" | "clear" }
  | { type: "click"; nx: number; ny: number }
  | { type: "move"; nx: number; ny: number }
  | { type: "down"; nx: number; ny: number }
  | { type: "up"; nx: number; ny: number }
  | { type: "wheel"; dx: number; dy: number; nx?: number; ny?: number }
  | { type: "key"; key: string }
  | { type: "text"; text: string }
  | { type: "navigate"; url: string }
  | { type: "back" }
  | { type: "forward" }
  | { type: "activate_tab"; index: number };

export type BrowserStreamStatus = "idle" | "connecting" | "open" | "closed";

const RECONNECT_BASE_DELAY_MS = 800;
const RECONNECT_MAX_DELAY_MS = 10_000;
const RECONNECT_MAX_ATTEMPTS = 6;

function normalizeSeedUrl(url: string | null | undefined): string {
  return (url ?? "").split("#", 1)[0]?.replace(/\/+$/, "") ?? "";
}

/**
 * Manage a live browser screencast WebSocket.
 *
 * When ``enabled`` is true, opens the stream, exposes the latest JPEG frame as
 * an object URL, and returns a ``sendInput`` callback that forwards user input
 * to the live page. Closes and cleans up when disabled or unmounted.
 *
 * ``seedUrl`` is only read when a connection is first established (via a ref, so
 * it is NOT a reconnect trigger). A separate effect steers an already-open live
 * page toward a changed seed with an in-band ``navigate`` event, so ordinary
 * navigations no longer tear down and rebuild the socket.
 */
export function useBrowserStream(
  threadId: string,
  enabled: boolean,
  seedUrl?: string,
  onNavRejected?: (
    url: string | undefined,
    message: string | undefined,
  ) => void,
) {
  const [manualLogin, setManualLogin] = useState<boolean | null>(null);
  const manualLoginRef = useRef(manualLogin);
  manualLoginRef.current = manualLogin;
  const [loginPending, setLoginPending] = useState(false);
  const [loginError, setLoginError] = useState(false);
  const [sessionLost, setSessionLost] = useState(false);
  const sessionIdRef = useRef<string | undefined>(undefined);
  const reconnectAttemptsRef = useRef(0);
  const [status, setStatus] = useState<BrowserStreamStatus>("idle");
  const [frameBuffer] = useState(() => new LatestBrowserFrameBuffer());
  const frameUrl = useSyncExternalStore(
    frameBuffer.subscribe,
    frameBuffer.getSnapshot,
    () => null,
  );
  const [liveUrl, setLiveUrl] = useState<string | null>(null);
  const [tabs, setTabs] = useState<BrowserTab[]>([]);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const pendingNavigateRef = useRef<Extract<
    BrowserInputEvent,
    { type: "navigate" }
  > | null>(null);
  // Read the seed at connect time only; it must not be a reconnect dependency.
  const seedRef = useRef(seedUrl);
  seedRef.current = seedUrl;
  // Latest live page URL reported by the server, used to decide whether an
  // open stream already shows the seed target (avoids redundant navigations).
  const liveUrlRef = useRef<string | null>(null);
  const onNavRejectedRef = useRef(onNavRejected);
  onNavRejectedRef.current = onNavRejected;

  const sendInput = useCallback((event: BrowserInputEvent) => {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      if (event.type === "login_control") {
        setLoginPending(true);
        setLoginError(false);
      }
      socket.send(JSON.stringify(event));
      return true;
    }
    // URL bar submissions are user intent and must not be lost during the
    // short Live connection window right after opening the panel.
    if (event.type === "navigate") {
      pendingNavigateRef.current = event;
    }
    return false;
  }, []);

  const reconnectNewSession = useCallback(() => {
    sessionIdRef.current = undefined;
    reconnectAttemptsRef.current = 0;
    setSessionLost(false);
    setLoginError(false);
    setConnectionAttempt((attempt) => attempt + 1);
  }, []);

  // An uncertain control must not be retried automatically. Release the UI
  // with an explicit error so a lost acknowledgement cannot spin forever.
  useEffect(() => {
    if (!loginPending) return;
    const timer = window.setTimeout(() => {
      setLoginPending(false);
      setLoginError(true);
    }, 30_000);
    return () => window.clearTimeout(timer);
  }, [loginPending]);

  useEffect(() => {
    pendingNavigateRef.current = null;
    setManualLogin(null);
    setLoginPending(false);
    setLoginError(false);
    setSessionLost(false);
    sessionIdRef.current = undefined;
    reconnectAttemptsRef.current = 0;
  }, [threadId]);

  useEffect(() => {
    if (enabled) {
      return;
    }
    setConnectionAttempt(0);
    frameBuffer.dispose();
    setLiveUrl(null);
    setTabs([]);
    liveUrlRef.current = null;
  }, [enabled, frameBuffer, threadId]);

  useEffect(() => {
    if (!enabled) {
      setStatus("idle");
      liveUrlRef.current = null;
      return;
    }

    let closedByEffect = false;
    let reconnectTimer: number | null = null;
    setStatus("connecting");
    setManualLogin(null);
    setLoginPending(false);
    // browserStreamURL treats empty/undefined seed identically (no seed param),
    // so the raw ref value is fine here. Record the seed optimistically so the
    // "steer to seed" effect below does not fire a duplicate navigate right
    // after open (the server already aligns the page to the connect-time seed).
    liveUrlRef.current = seedRef.current ?? null;
    const socket = new WebSocket(
      browserStreamURL(threadId, seedRef.current, sessionIdRef.current),
    );
    socket.binaryType = "blob";
    socketRef.current = socket;

    const scheduleReconnect = () => {
      if (closedByEffect || !enabled) {
        return;
      }
      if (reconnectTimer !== null) {
        return;
      }
      // Exponential backoff with a ceiling + attempt cap so a server that keeps
      // rejecting the upgrade cannot pin the client in a tight reconnect loop.
      if (reconnectAttemptsRef.current >= RECONNECT_MAX_ATTEMPTS) {
        return;
      }
      const delay = Math.min(
        RECONNECT_BASE_DELAY_MS * 2 ** reconnectAttemptsRef.current,
        RECONNECT_MAX_DELAY_MS,
      );
      reconnectTimer = window.setTimeout(() => {
        reconnectAttemptsRef.current += 1;
        setConnectionAttempt((attempt) => attempt + 1);
      }, delay);
    };

    socket.onopen = () => {
      const pendingNavigate = pendingNavigateRef.current;
      if (pendingNavigate) {
        socket.send(JSON.stringify(pendingNavigate));
        pendingNavigateRef.current = null;
      }
      // Reset the reconnect budget on a successful open. Without this the
      // cumulative attempt counter never returns to 0 while the panel stays
      // mounted, so after RECONNECT_MAX_ATTEMPTS total reconnects — even across
      // many healthy connections — scheduleReconnect would bail forever and
      // Live would go permanently dead until the panel is toggled off/on.
      // Reset only the retry budget, not the effect dependency. Changing the
      // connection key here used to tear down every successful reconnect.
      reconnectAttemptsRef.current = 0;
      setStatus("open");
    };
    socket.onmessage = (message) => {
      try {
        if (closedByEffect) return;
        if (message.data instanceof Blob) {
          frameBuffer.push(
            message.data.type === "image/jpeg"
              ? message.data
              : new Blob([message.data], { type: "image/jpeg" }),
          );
          return;
        }
        if (message.data instanceof ArrayBuffer) {
          frameBuffer.push(new Blob([message.data], { type: "image/jpeg" }));
          return;
        }
        if (typeof message.data !== "string") return;

        const payload = JSON.parse(message.data) as {
          type?: string;
          data?: string;
          url?: string;
          message?: string;
          tabs?: BrowserTab[];
          manual_login?: boolean;
          session_id?: string;
        };
        if (
          payload.type === "login_state" &&
          typeof payload.session_id === "string" &&
          /^[a-f0-9]{32}$/.test(payload.session_id)
        ) {
          sessionIdRef.current = payload.session_id;
        }
        if (
          payload.type === "login_state" &&
          typeof payload.manual_login === "boolean"
        ) {
          setManualLogin(payload.manual_login);
        } else if (payload.type === "login_ack") {
          setLoginPending(false);
          setLoginError(false);
        } else if (payload.type === "login_error") {
          setLoginPending(false);
          setLoginError(true);
        } else if (payload.type === "frame" && payload.data) {
          frameBuffer.replaceWithUrl(`data:image/jpeg;base64,${payload.data}`);
        } else if (payload.type === "url" && payload.url) {
          liveUrlRef.current = payload.url;
          setLiveUrl(payload.url);
        } else if (payload.type === "tabs" && Array.isArray(payload.tabs)) {
          setTabs(payload.tabs);
        } else if (payload.type === "nav_rejected") {
          onNavRejectedRef.current?.(payload.url, payload.message);
        }
      } catch (error) {
        console.warn("Ignoring malformed browser stream message", error);
      }
    };
    socket.onclose = (event) => {
      if (!closedByEffect) {
        setStatus("closed");
        if (event.code === 4410) {
          if (reconnectTimer !== null) {
            window.clearTimeout(reconnectTimer);
            reconnectTimer = null;
          }
          setSessionLost(true);
          setManualLogin(null);
          setLoginPending(false);
          frameBuffer.dispose();
          return;
        }
        scheduleReconnect();
      }
    };
    socket.onerror = () => {
      if (!closedByEffect) {
        setStatus("closed");
        scheduleReconnect();
      }
    };

    return () => {
      closedByEffect = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      socketRef.current = null;
      socket.close();
      frameBuffer.dispose();
    };
  }, [connectionAttempt, enabled, frameBuffer, threadId]);

  // Steer an already-open stream toward a changed seed in-band instead of
  // rebuilding the socket. Only navigates when the live page differs from the
  // seed target, so redirects/history moves the server already reflects do not
  // cause a redundant navigation loop.
  useEffect(() => {
    if (!enabled || status !== "open" || manualLoginRef.current !== false) {
      return;
    }
    const target = seedUrl?.trim();
    if (!target) {
      return;
    }
    if (normalizeSeedUrl(target) === normalizeSeedUrl(liveUrlRef.current)) {
      return;
    }
    sendInput({ type: "navigate", url: target });
  }, [enabled, seedUrl, sendInput, status]);

  return {
    status,
    frameUrl,
    liveUrl,
    tabs,
    sendInput,
    manualLogin,
    loginPending,
    loginError,
    sessionLost,
    reconnectNewSession,
  };
}
