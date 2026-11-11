export type ImThreadChannelType = "private" | "group";

export type ImThreadStatus = "active" | "archived" | "deleted" | "orphaned";

export type ImThreadItem = {
  thread_id: string;
  title: string | null;
  channel_type: ImThreadChannelType | string;
  chat_id: string;
  chat_name: string | null;
  last_active_at: string | null;
  is_current: boolean;
  status: ImThreadStatus | string;
};

export type ImGroupNode = {
  chat_id: string;
  name: string | null;
  name_source: string;
  chat_status: string;
  thread_count: number;
  last_active_at: string | null;
  current_thread_id: string | null;
  threads: ImThreadItem[];
};

export type ImPrivateSection = {
  threads: ImThreadItem[];
  total: number;
};

export type ImSessionTree = {
  private: ImPrivateSection;
  groups: ImGroupNode[];
  groups_total: number;
};

export type ImCapabilities = {
  im_session_tree_enabled: boolean;
  provider: string;
  max_group_threads: number;
  search_enabled: boolean;
};

export type ImThreadPage = {
  threads: ImThreadItem[];
  next_cursor: string | null;
};

export type ImSearchResponse = {
  results: ImThreadItem[];
  limit: number;
  offset: number;
};

export type ImThreadOrigin = {
  thread_id: string;
  owner_user_id: string;
  channel_type: string;
  chat_id: string;
  chat_name: string | null;
  chat_status: string | null;
  status: string;
  is_current: boolean;
  last_active_at: string | null;
  thread_exists: boolean;
};

export type ImSessionTreeParams = {
  groupsLimit?: number;
  threadsPerGroup?: number;
  groupOffset?: number;
};

export type ImThreadPageParams = {
  limit?: number;
  cursor?: string | null;
};