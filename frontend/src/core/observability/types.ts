export interface AuditLog {
  id: number;
  created_at: string;
  user_id: string | null;
  user_email: string | null;
  user_role: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  method: string;
  path: string;
  status_code: number;
  result: "success" | "failure" | string;
  duration_ms: number;
  client_ip: string | null;
  user_agent: string | null;
  error: string | null;
  details: Record<string, unknown>;
  operation_type: "api" | "management" | string;
  is_management: boolean;
}

export interface RecentError {
  run_id: string;
  thread_id: string;
  user_id: string | null;
  email: string | null;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface TraceSummary {
  trace_id: string;
  run_id: string;
  thread_id: string;
  user_id: string | null;
  email: string | null;
  status: string;
  model: string | null;
  agent: string | null;
  duration_ms: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  llm_calls: number;
  event_count: number;
  started_at: string;
  updated_at: string;
  error: string | null;
  error_type: string;
  user_question: string | null;
  tool_duration_ms?: number;
}

export interface TopUser {
  user_id: string;
  email: string | null;
  runs: number;
  tokens: number;
}

export interface ComponentHealth {
  name: string;
  status: "healthy" | "degraded" | "unknown" | string;
  detail: string;
  metric: string | null;
  latency_ms: number | null;
  last_checked_at: string;
  error: string | null;
}

export interface TraceFilters {
  hours?: number;
  start_at?: string;
  end_at?: string;
  limit?: number;
  q?: string;
  user_id?: string;
  status?: string;
  model?: string;
  agent?: string;
  thread_id?: string;
  run_id?: string;
  error_type?: string;
}

export interface AuditFilters {
  hours?: number;
  start_at?: string;
  end_at?: string;
  limit?: number;
  q?: string;
  user_id?: string;
  result?: string;
  operation_type?: string;
}

export interface ToolCallDiagnostic {
  id: string;
  name: string | null;
  args: unknown;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  result_preview: string | null;
  error: string | null;
}

export interface PossibleCause {
  type: string;
  label: string;
  confidence: string;
}

export interface RunDiagnostics {
  run: TraceSummary;
  user_question: string | null;
  agent: string | null;
  model: string | null;
  status: string;
  duration_ms: number;
  tokens: {
    input: number;
    output: number;
    total: number;
    llm_calls: number;
    by_model: Record<string, unknown>;
  };
  tool_calls: ToolCallDiagnostic[];
  error_stack: string | null;
  last_successful_node: string | null;
  failed_node: string | null;
  possible_causes: PossibleCause[];
  events: TraceEvent[];
  dispositions: OperationDisposition[];
  disposition_status: string | null;
}

export interface OperationDisposition {
  id: number;
  created_at: string;
  run_id: string;
  thread_id: string;
  action: string;
  status: string | null;
  note: string | null;
  user_id: string | null;
  user_email: string | null;
  result: string;
  details: Record<string, unknown>;
}

export interface TrendBucket {
  period_start: string;
  runs: number;
  error_runs: number;
  error_rate: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  lead_agent_tokens: number;
  subagent_tokens: number;
  middleware_tokens: number;
  avg_duration_ms: number;
  tool_duration_ms: number;
}

export interface ComparisonMetric {
  current: number;
  previous: number;
  delta: number;
  change_rate: number;
}

export interface CostBreakdown {
  key: string;
  email?: string | null;
  runs: number;
  error_runs: number;
  error_rate: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  lead_agent_tokens: number;
  subagent_tokens: number;
  middleware_tokens: number;
  tool_duration_ms: number;
  avg_duration_ms: number;
}

export interface ObservabilityAnalytics {
  generated_at: string;
  window_hours: number;
  window_start: string;
  window_end: string;
  bucket: "hour" | "day" | string;
  trend: TrendBucket[];
  current: Omit<TrendBucket, "period_start" | "tool_duration_ms">;
  previous: Omit<TrendBucket, "period_start" | "tool_duration_ms">;
  comparison: {
    runs: ComparisonMetric;
    error_rate: ComparisonMetric;
    tokens: ComparisonMetric;
    avg_duration_ms: ComparisonMetric;
  };
  slow_runs: TraceSummary[];
  high_token_runs: TraceSummary[];
  by_user: CostBreakdown[];
  by_model: CostBreakdown[];
  by_agent: CostBreakdown[];
}

export interface DispositionInput {
  action:
    | "copy_run_id"
    | "copy_thread_id"
    | "open_thread"
    | "retry_run"
    | "cancel_run"
    | "mark_handled"
    | "add_note";
  note?: string | null;
  status?: "open" | "handled" | "ignored" | null;
}

export interface ObservabilitySnapshot {
  generated_at: string;
  window_hours: number;
  window_start?: string;
  window_end?: string;
  health: {
    status: "healthy" | "degraded" | string;
    database: string;
    active_runs: number;
    error_rate: number;
  };
  component_health: ComponentHealth[];
  metrics: {
    users_total: number;
    active_users: number;
    threads_total: number;
    runs_total: number;
    runs_window: number;
    tokens_window: number;
    llm_calls_window: number;
    avg_run_duration_ms: number;
  };
  runs_by_status: Record<string, number>;
  runs_by_hour: Array<{ hour: string; runs: number }>;
  top_users: TopUser[];
  recent_errors: RecentError[];
  recent_traces: TraceSummary[];
  audit_logs: AuditLog[];
}

export interface TraceEvent {
  id: number;
  thread_id: string;
  run_id: string;
  seq: number;
  event_type: string;
  category: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string;
}
