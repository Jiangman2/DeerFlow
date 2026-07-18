export interface DashboardWindow {
  start_at: string;
  end_at: string;
  hours: number;
}

export interface DashboardOverview {
  total_users: number;
  new_users: number;
  active_users: number;
  threads: number;
  runs: number;
  successful_runs: number;
  failed_runs: number;
  running_runs: number;
  success_rate: number;
  avg_run_duration_ms: number;
  total_tokens: number;
  llm_calls: number;
  artifact_runs: number;
  knowledge_runs: number;
  feedback_total: number;
  satisfaction_rate: number;
  estimated_minutes_saved: number;
}

export interface UserActivityPoint {
  date: string;
  active_users: number;
  threads: number;
  runs: number;
}

export interface UserUsageItem {
  user_id: string;
  email: string | null;
  runs: number;
  threads: number;
  total_tokens: number;
  success_rate: number;
}

export interface ExternalAgentClickItem {
  name: string;
  clicks: number;
  users: number;
}

export interface FeatureUsageItem {
  name: string;
  count: number;
}

export interface AgentUsageItem {
  assistant_id: string | null;
  name: string;
  runs: number;
  users: number;
  success_rate: number;
  avg_duration_ms: number;
}

export interface ModelUsageItem {
  model_name: string;
  runs: number;
  users: number;
  success_rate: number;
  avg_duration_ms: number;
  total_tokens: number;
  llm_calls: number;
}

export interface DashboardFilters {
  hours?: number;
  start_at?: string;
  end_at?: string;
  status?: string;
  model?: string;
  agent?: string;
  feature?: string;
  value_profile?: "conservative" | "standard" | string;
}

export interface BusinessValueContribution {
  name: string;
  count: number;
  minutes_per_event: number;
  estimated_minutes: number;
}

export interface BusinessValueSummary {
  artifact_runs: number;
  knowledge_runs: number;
  file_processing_runs: number;
  presentation_runs: number;
  image_runs: number;
  estimated_minutes_saved: number;
  profile: string;
  contributions: BusinessValueContribution[];
}

export interface ArtifactTypeItem {
  type: string;
  count: number;
}

export interface RecentArtifactItem {
  run_id: string;
  thread_id: string;
  path: string;
  type: string;
  created_at: string;
}

export interface ArtifactSummary {
  total: number;
  by_type: ArtifactTypeItem[];
  previewable: number;
  recent: RecentArtifactItem[];
}

export interface FeedbackFeatureItem {
  name: string;
  positive: number;
  negative: number;
  satisfaction_rate: number;
}

export interface FeedbackSummary {
  total: number;
  positive: number;
  negative: number;
  satisfaction_rate: number;
  negative_rate: number;
  recent_comments: Array<{
    run_id: string;
    thread_id: string;
    rating: number;
    comment: string | null;
    created_at: string;
  }>;
  recent_negative: Array<{
    run_id: string;
    thread_id: string;
    rating: number;
    comment: string | null;
    created_at: string;
    diagnostics_url: string;
  }>;
  by_feature: FeedbackFeatureItem[];
}

export interface KnowledgeUsageSummary {
  runs: number;
  tool_calls: number;
  recent_tool_calls: Array<{
    run_id: string;
    thread_id: string;
    created_at: string;
    event_type: string;
    snippet: string;
  }>;
}

export interface RecentFailureItem {
  run_id: string;
  thread_id: string;
  user_id: string | null;
  email: string | null;
  status: string;
  error: string | null;
  assistant_id: string | null;
  model_name: string | null;
  started_at: string;
  updated_at: string;
  duration_ms: number;
  disposition_status: string | null;
  diagnostics_url: string;
  chat_url: string;
}

export interface RecentBusinessEventItem {
  run_id: string;
  thread_id: string;
  created_at: string;
  event_type: string;
  features: string[];
  snippet: string;
}

export interface DashboardSummary {
  window: DashboardWindow;
  overview: DashboardOverview;
  user_activity: UserActivityPoint[];
  user_usage: UserUsageItem[];
  external_agent_clicks: ExternalAgentClickItem[];
  feature_usage: FeatureUsageItem[];
  recent_business_events: RecentBusinessEventItem[];
  agent_usage: AgentUsageItem[];
  model_usage: ModelUsageItem[];
  business_value: BusinessValueSummary;
  artifact_summary: ArtifactSummary;
  feedback: FeedbackSummary;
  knowledge_usage: KnowledgeUsageSummary;
  recent_failures: RecentFailureItem[];
}
