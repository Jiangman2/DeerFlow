import { throwGatewayApiError } from "@/core/api/errors";
import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import { readDiagnosticResponse } from "./diagnostic-response";
import type {
  AuditFilters,
  AuditLog,
  DispositionInput,
  ObservabilityAnalytics,
  ObservabilitySnapshot,
  RunDiagnostics,
  TraceEvent,
  TraceFilters,
  TraceSummary,
  UserActivityReport,
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    await throwGatewayApiError(
      response,
      `服务暂时不可用（HTTP ${response.status}），请稍后重试`,
    );
  }
  return (await response.json()) as T;
}

export async function getObservabilitySnapshot(
  hours = 24,
  startAt?: string,
  endAt?: string,
): Promise<ObservabilitySnapshot> {
  const params = new URLSearchParams({
    hours: String(hours),
    limit: "80",
  });
  appendParams(params, {
    start_at: startAt,
    end_at: endAt,
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/snapshot?${params}`,
  );
  return readJson<ObservabilitySnapshot>(response);
}

export async function getObservabilityAnalytics(
  hours = 24,
  bucket: "hour" | "day" = "hour",
  startAt?: string,
  endAt?: string,
): Promise<ObservabilityAnalytics> {
  const params = new URLSearchParams({
    hours: String(hours),
    bucket,
    limit: "10",
  });
  appendParams(params, {
    start_at: startAt,
    end_at: endAt,
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/analytics?${params}`,
  );
  return readJson<ObservabilityAnalytics>(response);
}

export async function getUserActivity(
  hours = 24,
  startAt?: string,
  endAt?: string,
  limit = 100,
): Promise<UserActivityReport> {
  const params = new URLSearchParams({
    hours: String(hours),
    limit: String(limit),
  });
  appendParams(params, {
    start_at: startAt,
    end_at: endAt,
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/user-activity?${params}`,
  );
  return readJson<UserActivityReport>(response);
}

export async function getAuditLogs(limit = 100): Promise<AuditLog[]> {
  const response = await fetchObservabilityAuditLogs({ limit });
  const data = await readJson<{ items: AuditLog[] }>(response);
  return data.items;
}

function appendParams(
  params: URLSearchParams,
  values: Record<string, string | number | undefined>,
) {
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "" && value !== "all") {
      params.set(key, String(value));
    }
  }
}

function fetchObservabilityAuditLogs(filters: AuditFilters) {
  const params = new URLSearchParams();
  appendParams(params, {
    hours: filters.hours,
    start_at: filters.start_at,
    end_at: filters.end_at,
    limit: filters.limit ?? 100,
    q: filters.q,
    user_id: filters.user_id,
    result: filters.result,
    operation_type: filters.operation_type,
  });
  return fetch(
    `${getBackendBaseURL()}/api/admin/observability/audit-logs?${params}`,
  );
}

export async function searchAuditLogs(
  filters: AuditFilters,
): Promise<AuditLog[]> {
  const response = await fetchObservabilityAuditLogs(filters);
  const data = await readJson<{ items: AuditLog[] }>(response);
  return data.items;
}

export async function searchTraces(
  filters: TraceFilters,
): Promise<TraceSummary[]> {
  const params = new URLSearchParams();
  appendParams(params, {
    hours: filters.hours ?? 24,
    start_at: filters.start_at,
    end_at: filters.end_at,
    limit: filters.limit ?? 100,
    q: filters.q,
    user_id: filters.user_id,
    status: filters.status,
    model: filters.model,
    agent: filters.agent,
    thread_id: filters.thread_id,
    run_id: filters.run_id,
    error_type: filters.error_type,
  });
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/traces?${params}`,
  );
  const data = await readJson<{ items: TraceSummary[] }>(response);
  return data.items;
}

export async function getRunDiagnostics(
  runId: string,
  signal?: AbortSignal,
): Promise<RunDiagnostics> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 20_000);
  try {
    const response = await fetch(
      `${getBackendBaseURL()}/api/admin/observability/runs/${encodeURIComponent(runId)}/diagnostics`,
      { signal: controller.signal },
    );
    return await readDiagnosticResponse<RunDiagnostics>(response);
  } catch (error) {
    if (timedOut) throw new Error("错误详情加载超时，请稍后重试");
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

export async function recordRunDisposition(
  runId: string,
  input: DispositionInput,
): Promise<{ item: unknown; details: Record<string, unknown> }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/runs/${encodeURIComponent(
      runId,
    )}/dispositions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  );
  return readJson<{ item: unknown; details: Record<string, unknown> }>(
    response,
  );
}

export async function getTraceEvents(runId: string): Promise<TraceEvent[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/admin/observability/traces/${encodeURIComponent(
      runId,
    )}/events`,
  );
  const data = await readJson<{ items: TraceEvent[] }>(response);
  return data.items;
}
