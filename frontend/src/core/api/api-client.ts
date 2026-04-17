"use client";

import { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

import { getLangGraphBaseURL } from "../config";

import { sanitizeRunStreamOptions } from "./stream-mode";

function createCompatibleClient(isMock?: boolean): LangGraphClient {
  const customFetch: typeof fetch = async (input, init) => {
    let response: Response;
    try {
      response = await fetch(input, init);
    } catch (e) {
      const rawUrl =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      const base =
        typeof window !== "undefined" ? window.location.origin : "http://localhost";
      const url = new URL(rawUrl, base);
      const isStream =
        url.pathname.includes("/runs/stream") || url.pathname.endsWith("/stream");
      const aborted =
        Boolean(init?.signal?.aborted) ||
        (typeof e === "object" &&
          e !== null &&
          "name" in e &&
          (e as { name?: unknown }).name === "AbortError") ||
        (e instanceof Error && e.message.includes("aborted")) ||
        (Boolean(init?.signal) &&
          isStream &&
          e instanceof TypeError &&
          e.message.includes("fetch"));
      if (!aborted) throw e;
      if (!isStream) {
        throw e;
      }
      return new Response(null, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      });
    }
    try {
      const rawUrl =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      const base =
        typeof window !== "undefined" ? window.location.origin : "http://localhost";
      const url = new URL(rawUrl, base);
      if (response.status === 409) {
        const method = (init?.method ?? "GET").toUpperCase();
        const isCreateThreadEndpoint =
          /\/threads\/?$/.test(url.pathname) && !url.pathname.includes("/runs");
        if (method === "POST" && isCreateThreadEndpoint) {
          const text = await response.clone().text();
          if (text.includes("Thread with ID") && text.includes("already exists")) {
            let threadId: string | null = null;
            let metadata: Record<string, unknown> = {};
            const body = init?.body;
            if (typeof body === "string" && body.trim()) {
              try {
                const parsed = JSON.parse(body) as {
                  thread_id?: unknown;
                  metadata?: unknown;
                };
                if (typeof parsed.thread_id === "string" && parsed.thread_id.trim()) {
                  threadId = parsed.thread_id.trim();
                }
                if (
                  typeof parsed.metadata === "object" &&
                  parsed.metadata !== null &&
                  !Array.isArray(parsed.metadata)
                ) {
                  metadata = parsed.metadata as Record<string, unknown>;
                }
              } catch {
                threadId = null;
              }
            }
            if (!threadId) {
              try {
                const parsed = JSON.parse(text) as { detail?: unknown };
                if (typeof parsed.detail === "string") {
                  const m = parsed.detail.match(
                    /Thread with ID\s+([0-9a-fA-F-]{16,})\s+already exists/,
                  );
                  if (m?.[1]) threadId = m[1];
                }
              } catch {
                threadId = null;
              }
            }
            if (threadId) {
              return new Response(
                JSON.stringify({
                  thread_id: threadId,
                  created_at: new Date().toISOString(),
                  metadata,
                }),
                {
                status: 200,
                headers: { "content-type": "application/json" },
                },
              );
            }
          }
        }
      }
      const isCancel =
        url.pathname.endsWith("/cancel") || url.pathname.endsWith("/runs/cancel");
      if (!isCancel) {
        return response;
      }
      if (response.status === 404) {
        return new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      if (response.status === 400) {
        const text = await response.clone().text();
        if (text.includes("No matching runs to cancel")) {
          return new Response("{}", {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
      }
    } catch {
      return response;
    }
    return response;
  };

  const client = new LangGraphClient({
    apiUrl: getLangGraphBaseURL(isMock),
    callerOptions: { fetch: customFetch },
  });

  const originalRunStream = client.runs.stream.bind(client.runs);
  client.runs.stream = ((threadId, assistantId, payload) =>
    originalRunStream(
      threadId,
      assistantId,
      sanitizeRunStreamOptions(payload),
    )) as typeof client.runs.stream;

  const originalJoinStream = client.runs.joinStream.bind(client.runs);
  client.runs.joinStream = ((threadId, runId, options) =>
    originalJoinStream(
      threadId,
      runId,
      sanitizeRunStreamOptions(options),
    )) as typeof client.runs.joinStream;

  return client;
}

let _singleton: LangGraphClient | null = null;
export function getAPIClient(isMock?: boolean): LangGraphClient {
  _singleton ??= createCompatibleClient(isMock);
  return _singleton;
}
