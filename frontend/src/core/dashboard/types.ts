export interface DashboardWindow {
  start_at: string;
  end_at: string;
  hours: number;
}

export interface DashboardDateBounds {
  earliest_at: string | null;
  selectable_start_at: string | null;
  timezone: string;
}

export interface DashboardOverview {
  total_users: number;
  new_users: number;
  active_users: number;
  threads: number;
  all_time_threads: number;
  existing_threads: number;
  runs: number;
  terminal_runs: number;
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
  end_date: string;
  active_users: number;
  threads: number;
  runs: number;
}

export interface UserUsageItem {
  user_id: string;
  email: string | null;
  display_name: string | null;
  organization_name: string | null;
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

export interface ExternalPlatformSourceMetric {
  source: string;
  today_users: number;
  total_users: number;
  today_clicks: number;
  total_clicks: number;
}

export interface ExternalPlatformAgentMetric {
  name: string;
  today_users: number;
  total_users: number;
  today_clicks: number;
  total_clicks: number;
  by_source: ExternalPlatformSourceMetric[];
}

export interface ExternalPlatformClickSummary {
  available: boolean;
  error: string | null;
  today: string;
  days: number;
  agents: ExternalPlatformAgentMetric[];
  trend: {
    dates: string[];
    series: Record<string, number[]>;
  };
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
  available: boolean;
  error: string | null;
  current_users: number;
  registered_users: number;
  current_knowledge_bases: number;
  created_knowledge_bases: number;
  current_documents: number;
  uploaded_documents: number;
  current_conversation_sessions: number;
  conversation_sessions: number;
  current_question_turns: number;
  question_turns: number;
  current_shared_spaces: number;
  created_shared_spaces: number;
  current_shared_space_members: number;
  joined_shared_space_members: number;
  current_shared_knowledge_bases: number;
  shared_knowledge_bases: number;
  current_shared_documents: number;
  uploaded_shared_documents: number;
  pending_shared_space_requests: number;
  shared_space_join_requests: number;
  file_formats: KnowledgeFileFormatUsageItem[];
  knowledge_bases: KnowledgeBaseUsageItem[];
  users: KnowledgeUserUsageItem[];
  shared_spaces: SharedSpaceUsageItem[];
}

export interface KnowledgeFileFormatUsageItem {
  format: string;
  current_count: number;
  uploaded_count: number;
}

export interface KnowledgeBaseUsageItem {
  knowledge_base_id: string;
  name: string;
  creator_email: string | null;
  creator_name: string | null;
  current_documents: number;
  uploaded_documents: number;
  current_conversation_sessions: number;
  conversation_sessions: number;
  current_question_turns: number;
  question_turns: number;
  file_formats: KnowledgeFileFormatUsageItem[];
}

export interface KnowledgeUserUsageItem {
  weknora_user_id: string;
  email: string | null;
  name: string | null;
  current_knowledge_bases: number;
  created_knowledge_bases: number;
  current_documents: number;
  uploaded_documents: number;
  conversation_sessions: number;
  question_turns: number;
}

export interface SharedSpaceUsageItem {
  shared_space_id: string;
  name: string;
  description: string | null;
  created_at: string;
  owner_email: string | null;
  owner_name: string | null;
  current_members: number;
  joined_members: number;
  admins: number;
  editors: number;
  viewers: number;
  current_knowledge_bases: number;
  shared_knowledge_bases: number;
  current_documents: number;
  uploaded_documents: number;
  pending_requests: number;
  join_requests: number;
}

export interface ScheduledTaskUserUsageItem {
  user_id: string;
  email: string | null;
  display_name: string | null;
  organization_name: string | null;
  current_tasks: number;
  created_tasks: number;
  enabled_tasks: number;
  paused_tasks: number;
  runs: number;
  successful_runs: number;
  failed_runs: number;
  skipped_runs: number;
}

export interface ScheduledTaskUsageSummary {
  current_tasks: number;
  created_tasks: number;
  enabled_tasks: number;
  paused_tasks: number;
  runs: number;
  successful_runs: number;
  failed_runs: number;
  skipped_runs: number;
  users: ScheduledTaskUserUsageItem[];
}

export interface RecentFailureItem {
  run_id: string;
  thread_id: string;
  user_id: string | null;
  email: string | null;
  display_name: string | null;
  status: string;
  error: string | null;
  assistant_id: string | null;
  model_name: string | null;
  started_at: string;
  updated_at: string;
  duration_ms: number;
  disposition_status: string | null;
  diagnostics_url: string;
  can_open: boolean;
  chat_url: string | null;
}

export interface FailedRunList {
  items: RecentFailureItem[];
  total: number;
}

export interface RecentBusinessEventItem {
  run_id: string;
  thread_id: string;
  created_at: string;
  event_type: string;
  features: string[];
  snippet: string;
}

export interface DashboardAggregationInfo {
  source: "raw" | "rollup" | "rollup+raw" | string;
  schema_version: number;
  rollup_days: number;
  rollup_hours: number;
  raw_segments: number;
}

export interface DashboardRunRankingItem {
  run_id: string;
  thread_id: string;
  model: string | null;
  agent: string | null;
  duration_ms: number;
  tokens: number;
}

export interface DashboardRunRankings {
  slow_runs: DashboardRunRankingItem[];
  high_token_runs: DashboardRunRankingItem[];
}

export interface DashboardSummary {
  window: DashboardWindow;
  overview: DashboardOverview;
  activity_bucket: "day" | "week" | string;
  user_activity: UserActivityPoint[];
  user_usage: UserUsageItem[];
  external_agent_clicks: ExternalAgentClickItem[];
  external_platform_agent_clicks: ExternalPlatformClickSummary;
  feature_usage: FeatureUsageItem[];
  recent_business_events: RecentBusinessEventItem[];
  agent_usage: AgentUsageItem[];
  model_usage: ModelUsageItem[];
  business_value: BusinessValueSummary;
  artifact_summary: ArtifactSummary;
  feedback: FeedbackSummary;
  knowledge_usage: KnowledgeUsageSummary;
  scheduled_task_usage: ScheduledTaskUsageSummary;
  recent_failures: RecentFailureItem[];
  aggregation: DashboardAggregationInfo;
}

export interface RegisteredUserSessionSummary {
  user_id: string;
  email: string;
  display_name: string | null;
  organization_name: string | null;
  system_role: string;
  is_disabled: boolean;
  registered_at: string;
  last_login_at: string | null;
  existing_threads: number;
  runs: number;
  terminal_runs: number;
  successful_runs: number;
  failed_runs: number;
  running_runs: number;
  last_active_at: string | null;
}

export interface RegisteredUserSessionList {
  items: RegisteredUserSessionSummary[];
  total: number;
}

export interface ExistingSessionItem {
  thread_id: string;
  title: string | null;
  assistant_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  runs: number;
  terminal_runs: number;
  successful_runs: number;
  failed_runs: number;
  running_runs: number;
  last_run_at: string | null;
  last_run_status: string | null;
  can_open: boolean;
  chat_url: string | null;
}

export interface ExistingSessionList {
  user_id: string;
  items: ExistingSessionItem[];
  total: number;
}
