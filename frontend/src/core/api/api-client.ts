"use client";

import { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

import { getLangGraphBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import {
  loadStaticDemoThread,
  loadStaticDemoThreads,
  staticDemoThreadState,
  isStaticDemoThreadId,
} from "../threads/static-demo";
import type { AgentThreadState } from "../threads/types";

import { isStateChangingMethod, readCsrfCookie } from "./fetcher";
import {
  getPptMasterRuntimeBaseURL,
  PPT_MASTER_RUNTIME_ASSISTANT_ID,
  PPT_MASTER_RUNTIME_METADATA_KEY,
  shouldUsePptMasterRuntime,
} from "./ppt-master-runtime";
import { sanitizeRunStreamOptions } from "./stream-mode";

/**
 * SDK ``onRequest`` hook that mints the ``X-CSRF-Token`` header from the
 * live ``csrf_token`` cookie just before each outbound fetch.
 *
 * Reading the cookie per-request (rather than baking it into the SDK's
 * ``defaultHeaders`` at construction) handles login / logout / password
 * change cookie rotation transparently. Both the ``/api/langgraph/*`` SDK
 * path and the direct REST endpoints in ``fetcher.ts:fetchWithAuth``
 * share :func:`readCsrfCookie` and :const:`STATE_CHANGING_METHODS` so
 * the contract stays in lockstep.
 */
function injectCsrfHeader(_url: URL, init: RequestInit): RequestInit {
  if (!isStateChangingMethod(init.method ?? "GET")) {
    return init;
  }
  const token = readCsrfCookie();
  if (!token) return init;
  const headers = new Headers(init.headers);
  if (!headers.has("X-CSRF-Token")) {
    headers.set("X-CSRF-Token", token);
  }
  return { ...init, headers };
}

// Run statuses that have reached a terminal state where no further streaming
// is possible. Reconnecting (``joinStream``) to such a run either 409s or, once
// the backend's in-memory stream bridge is reaped (``worker.py`` calls
// ``publish_end`` unconditionally, including for interrupted runs, then reaps
// the bridge after 60s), blocks forever on a drained condition variable —
// pinning ``isLoading`` true so the submit button stays a stop button and the
// first message after a reload never sends. The ``joinStream`` wrapper below
// short-circuits these *before* joining.
//
// ``interrupted`` is included because it is only ever written by
// ``RunManager.cancel()`` (a user-initiated stop); the resumable human-in-the-
// loop path uses ``Command(goto=END)`` (``ClarificationMiddleware``), which
// ends the run as ``success``, not ``interrupted``. So an interrupted run has
// nothing left to stream — its state lives in the checkpoint, fetched
// independently by ``useThreadHistory``, and resuming means a fresh ``submit``.
//
// ``error``/``timeout`` are terminal too, so a reload within the ~60s
// bridge-reap window no longer replays the buffered error event through
// ``onError`` — the transient error toast (``getStreamErrorMessage``) is
// dropped. The persisted error state still loads from the checkpoint via
// ``useThreadHistory``, so only the toast is lost; that is intentional, since
// surfacing a stale error toast on every reload is noise rather than signal.
const TERMINAL_RUN_STATUSES = new Set([
  "success",
  "error",
  "timeout",
  "interrupted",
]);

type ReconnectInputSnapshot = Record<string, unknown> & {
  messages: unknown[];
};

function streamOptionSignal(options: unknown): AbortSignal | undefined {
  if (typeof AbortSignal === "undefined") {
    return undefined;
  }
  if (options instanceof AbortSignal) {
    return options;
  }
  if (typeof options !== "object" || options === null) {
    return undefined;
  }
  const signal = Reflect.get(options, "signal");
  return signal instanceof AbortSignal ? signal : undefined;
}

/**
 * Restore the submitted human input before replaying an active run.
 * Incremental reconnect streams can deliver AI/tool chunks before the input
 * has reached durable thread history. The run record retains that input in
 * ``kwargs.input``; merge it into one synthetic values snapshot and de-dupe
 * messages that the checkpoint already contains.
 *
 * Read failures intentionally fall back to the original reconnect behavior.
 * Abort errors are rethrown so callers can still cancel reconnect hydration.
 */
async function loadReconnectInputSnapshot(
  client: LangGraphClient,
  threadId: string,
  run: Awaited<ReturnType<LangGraphClient["runs"]["get"]>>,
  signal?: AbortSignal,
): Promise<ReconnectInputSnapshot | undefined> {
  try {
    const runKwargs = Reflect.get(run, "kwargs");
    const input =
      typeof runKwargs === "object" && runKwargs !== null
        ? Reflect.get(runKwargs, "input")
        : undefined;
    const inputMessages =
      typeof input === "object" && input !== null
        ? Reflect.get(input, "messages")
        : undefined;
    if (!Array.isArray(inputMessages) || inputMessages.length === 0) {
      return undefined;
    }

    const durableValues = (
      await client.threads.getState(threadId, undefined, { signal })
    ).values;
    const normalizedDurableValues =
      typeof durableValues === "object" && durableValues !== null
        ? durableValues
        : {};
    const durableMessages = Array.isArray(
      Reflect.get(normalizedDurableValues, "messages"),
    )
      ? (Reflect.get(normalizedDurableValues, "messages") as unknown[])
      : [];
    const seenIds = new Set(
      durableMessages.flatMap((message) => {
        const id =
          typeof message === "object" && message !== null
            ? Reflect.get(message, "id")
            : undefined;
        return typeof id === "string" && id.length > 0 ? [id] : [];
      }),
    );
    const messages = [
      ...durableMessages,
      ...inputMessages.filter((message) => {
        const id =
          typeof message === "object" && message !== null
            ? Reflect.get(message, "id")
            : undefined;
        if (typeof id !== "string" || id.length === 0) return true;
        if (seenIds.has(id)) return false;
        seenIds.add(id);
        return true;
      }),
    ];
    return { ...normalizedDurableValues, messages } as ReconnectInputSnapshot;
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") {
      throw error;
    }
    return undefined;
  }
}

/**
 * Shared matcher for the gateway's 409 conflict responses. The SDK surfaces
 * non-2xx responses as ``HTTPError { status, message }`` where ``message`` is
 * ``"HTTP 409: {\"detail\":\"...\"}"``, so a 409 may be detected either via the
 * numeric ``status`` or a substring of ``message``.
 *
 * Every passed ``needles`` substring must be present; this AND semantics is what
 * lets a caller distinguish sibling conflict branches by phrase (e.g. the
 * terminal-state cancel branch from the still-active-on-another-worker branch).
 *
 * Match strings until the API exposes a structured error code; the source of
 * truth is ``_cancel_conflict_detail`` / the store-only response in
 * ``backend/app/gateway/routers/thread_runs.py``.
 */
function isRunConflictError(error: unknown, ...needles: string[]): boolean {
  const status =
    typeof error === "object" && error !== null
      ? Reflect.get(error, "status")
      : undefined;
  const message =
    typeof error === "string"
      ? error
      : error instanceof Error
        ? error.message
        : typeof error === "object" && error !== null
          ? String(Reflect.get(error, "message") ?? "")
          : "";

  return (
    (status === 409 || message.includes("HTTP 409")) &&
    needles.every((needle) => message.includes(needle))
  );
}

// Store-only run cannot be streamed (no in-memory stream bridge on this
// worker): reconnect has nothing to rejoin.
export function isInactiveRunStreamError(error: unknown): boolean {
  return isRunConflictError(
    error,
    "not active on this worker",
    "cannot be streamed",
  );
}

/**
 * Matches the gateway's terminal-state cancel conflict, raised by
 * ``_cancel_conflict_detail`` in ``backend/app/gateway/routers/thread_runs.py``
 * as ``Run X is not cancellable (status: success|error|timeout)`` when
 * ``RunManager.cancel`` refuses a run that already finished.
 *
 * The sibling ``"not active on this worker and cannot be cancelled"`` branch
 * (run still pending/running on another worker in a multi-instance deploy) is
 * intentionally NOT matched — that is a real cancel failure on a live run and
 * must stay visible. Only the terminal-state branch is a true no-op.
 */
export function isRunNotCancellableError(error: unknown): boolean {
  return isRunConflictError(error, "is not cancellable");
}

/**
 * Preflight a reconnect and return the run record when it can be read. A
 * missing record or failed request returns ``undefined`` so a legitimately
 * active reconnect falls back to the original join.
 *
 * Any error (404 for an evicted record, network blip, auth hiccup, …) falls
 * back to the original join so a legitimately active reconnect is never
 * silently suppressed.
 */
async function getReconnectRun(
  client: LangGraphClient,
  threadId: string,
  runId: string,
  signal?: AbortSignal,
): Promise<Awaited<ReturnType<LangGraphClient["runs"]["get"]>> | undefined> {
  try {
    return await client.runs.get(threadId, runId, { signal });
  } catch {
    return undefined;
  }
}

export function clearReconnectRun(
  threadId: string | null | undefined,
  runId: string,
): void {
  if (typeof window === "undefined" || !threadId) return;

  const key = `lg:stream:${threadId}`;
  try {
    const storage = window.sessionStorage;
    if (storage.getItem(key) === runId) {
      storage.removeItem(key);
    }
  } catch {
    // Ignore storage access failures so reconnect cleanup never throws.
  }
}

function createCompatibleClient(
  isMock?: boolean,
  staticDemoThreadId?: string,
): LangGraphClient {
  if (
    !isMock &&
    (isStaticWebsiteOnly() || isStaticDemoThreadId(staticDemoThreadId))
  ) {
    return createStaticClient();
  }

  const apiUrl = getLangGraphBaseURL(isMock);
  console.log(`Creating API client with base URL: ${apiUrl}`);
  const client = new LangGraphClient({
    apiUrl,
    onRequest: injectCsrfHeader,
  });
  const pptMasterClient = isMock
    ? null
    : new LangGraphClient({
        apiUrl: getPptMasterRuntimeBaseURL(),
        onRequest: injectCsrfHeader,
      });

  const originalRunStream = client.runs.stream.bind(client.runs);

  // Explicit ppt-master threads are checkpointed by the isolated runtime on
  // port 8003.  Keep read-side state/history on that same namespace.  Sending
  // these reads to the ordinary LangGraph runtime produces misleading 404s
  // and prevents artifact/history hydration after a page reload even while
  // the external Agent turn itself is still healthy.
  const originalGetThread = client.threads.get.bind(client.threads);
  client.threads.get = (async (threadId, options) => {
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId)),
    );
    return usePptMaster && pptMasterClient
      ? pptMasterClient.threads.get(threadId, options)
      : originalGetThread(threadId, options);
  }) as typeof client.threads.get;

  const originalGetState = client.threads.getState.bind(client.threads);
  client.threads.getState = (async (threadId, checkpoint, options) => {
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId)),
    );
    return usePptMaster && pptMasterClient
      ? pptMasterClient.threads.getState(threadId, checkpoint, options)
      : originalGetState(threadId, checkpoint, options);
  }) as typeof client.threads.getState;

  const originalGetHistory = client.threads.getHistory.bind(client.threads);
  client.threads.getHistory = (async (threadId, options) => {
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId)),
    );
    return usePptMaster && pptMasterClient
      ? pptMasterClient.threads.getHistory(threadId, options)
      : originalGetHistory(threadId, options);
  }) as typeof client.threads.getHistory;

  client.runs.stream = async function* (threadId, assistantId, payload) {
    const sanitized = sanitizeRunStreamOptions(payload);
    const input = Reflect.get(sanitized as object, "input");
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId, input)),
    );
    if (usePptMaster && pptMasterClient) {
      const metadata = Reflect.get(sanitized as object, "metadata");
      const runnerPayload = {
        ...sanitized,
        metadata: {
          ...(typeof metadata === "object" && metadata !== null
            ? metadata
            : {}),
          [PPT_MASTER_RUNTIME_METADATA_KEY]: PPT_MASTER_RUNTIME_ASSISTANT_ID,
        },
      };
      yield* pptMasterClient.runs.stream(
        threadId,
        PPT_MASTER_RUNTIME_ASSISTANT_ID,
        runnerPayload,
      );
      return;
    }
    yield* originalRunStream(threadId, assistantId, sanitized);
  } as typeof client.runs.stream;

  const originalCancel = client.runs.cancel.bind(client.runs);
  client.runs.cancel = (async (threadId, runId, wait, action, options) => {
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId)),
    );
    const cancel = usePptMaster
      ? pptMasterClient!.runs.cancel.bind(pptMasterClient!.runs)
      : originalCancel;
    try {
      return await cancel(threadId, runId, wait, action, options);
    } catch (error) {
      if (isRunNotCancellableError(error)) {
        // The run already reached a terminal state, so cancelling it is a
        // no-op. Swallow the 409 so a stop click during the finish window
        // (backend flipped to ``success`` but the SSE stream hasn't drained)
        // doesn't surface as an unhandled rejection, and clear the now-stale
        // reconnect key. clearReconnectRun only removes the key when it still
        // matches this runId, so a newer run's key is never touched.
        clearReconnectRun(threadId, runId);
        return;
      }
      throw error;
    }
  }) as typeof client.runs.cancel;

  const originalJoinStream = client.runs.joinStream.bind(client.runs);
  client.runs.joinStream = async function* (threadId, runId, options) {
    const usePptMaster = Boolean(
      pptMasterClient &&
      threadId &&
      (await shouldUsePptMasterRuntime(client, threadId)),
    );
    const streamClient = usePptMaster ? pptMasterClient! : client;
    const joinStream = usePptMaster
      ? pptMasterClient!.runs.joinStream.bind(pptMasterClient!.runs)
      : originalJoinStream;
    const reconnectSignal = streamOptionSignal(options);
    const reconnectRun = threadId
      ? await getReconnectRun(streamClient, threadId, runId, reconnectSignal)
      : undefined;
    // Short-circuit reconnects to runs that have already finished: otherwise a
    // reload after the backend's stream bridge is reaped blocks forever on a
    // drained condition variable, pinning ``isLoading`` true so the first
    // post-reload message is routed to ``stop`` instead of ``submit``.
    if (reconnectRun && TERMINAL_RUN_STATUSES.has(reconnectRun.status)) {
      clearReconnectRun(threadId, runId);
      return;
    }
    if (threadId && reconnectRun) {
      const reconnectSnapshot = await loadReconnectInputSnapshot(
        streamClient,
        threadId,
        reconnectRun,
        reconnectSignal,
      );
      if (reconnectSnapshot) {
        // Internal hydration frame: show the submitted human message before
        // incremental AI/tool chunks arrive. Later copies are de-duplicated by
        // message id by the normal thread state pipeline.
        yield { event: "values", data: reconnectSnapshot };
      }
    }
    try {
      yield* joinStream(threadId, runId, sanitizeRunStreamOptions(options));
    } catch (error) {
      if (isInactiveRunStreamError(error)) {
        clearReconnectRun(threadId, runId);
        return;
      }
      throw error;
    }
  } as typeof client.runs.joinStream;

  return client;
}

function createStaticClient(): LangGraphClient {
  const apiUrl =
    typeof window === "undefined"
      ? "http://localhost:3000"
      : window.location.origin;
  const client = new LangGraphClient({ apiUrl });

  client.threads.search = (async (query) => {
    return loadStaticDemoThreads(query);
  }) as typeof client.threads.search;

  client.threads.get = (async (threadId) => {
    return loadStaticDemoThread(threadId);
  }) as typeof client.threads.get;

  client.threads.getState = (async (threadId) => {
    return staticDemoThreadState(await loadStaticDemoThread(threadId));
  }) as typeof client.threads.getState;

  client.threads.getHistory = (async (threadId) => {
    return [staticDemoThreadState(await loadStaticDemoThread(threadId))];
  }) as typeof client.threads.getHistory;

  client.threads.update = (async (threadId) => {
    return loadStaticDemoThread(threadId);
  }) as typeof client.threads.update;

  client.runs.list = (async () => []) as typeof client.runs.list;
  client.runs.stream = async function* () {
    /* empty */
  } as typeof client.runs.stream;
  client.runs.joinStream = async function* () {
    /* empty */
  } as typeof client.runs.joinStream;

  return client as LangGraphClient<AgentThreadState>;
}

const _clients = new Map<string, LangGraphClient>();
export function getAPIClient(
  isMock?: boolean,
  staticDemoThreadId?: string,
): LangGraphClient {
  const cacheKey = isMock
    ? "mock"
    : isStaticDemoThreadId(staticDemoThreadId)
      ? `static-demo:${staticDemoThreadId}`
      : "default";
  let client = _clients.get(cacheKey);

  if (!client) {
    client = createCompatibleClient(isMock, staticDemoThreadId);
    _clients.set(cacheKey, client);
  }

  return client;
}
