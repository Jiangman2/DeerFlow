export interface WeKnoraKnowledgeBase {
  id: string;
  name: string;
  description?: string;
  type?: string;
  access_scope?: "owned" | "joined" | "team" | "public" | string;
  is_owner?: boolean;
  is_joined?: boolean;
  is_team_or_public?: boolean;
  tenant_id?: string | number;
  owner_tenant_id?: string;
  access_tenant_id?: string;
  tenant_name?: string;
  membership_role?: string;
  creator_id?: string;
  share_id?: string;
  organization_id?: string;
  org_name?: string;
  permission?: string;
  source_tenant_id?: string;
  shared_at?: string;
  share_count?: number;
  knowledge_count?: number;
  chunk_count?: number;
  processing_count?: number;
  is_processing?: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface WeKnoraKnowledge {
  id: string;
  knowledge_base_id?: string;
  title?: string;
  description?: string;
  file_name?: string;
  file_type?: string;
  file_size?: number;
  source?: string;
  channel?: string;
  parse_status?: string;
  summary_status?: string;
  enable_status?: string;
  error_message?: string;
  created_at?: string;
  updated_at?: string;
  processed_at?: string | null;
  tag_id?: string;
  tags?: WeKnoraTag[];
}

export interface WeKnoraTag {
  id: string;
  name: string;
  knowledge_base_id?: string;
  knowledge_count?: number;
  created_at?: string;
  updated_at?: string;
}

export type WeKnoraScopeItemType = "kb" | "file" | "tag";

export interface WeKnoraScopeItem {
  id: string;
  name: string;
  type: WeKnoraScopeItemType;
  kb_id?: string;
  kb_name?: string;
  kb_type?: string;
  tenant_id?: string;
}

export interface WeKnoraTagScope {
  knowledge_base_id: string;
  tag_ids: string[];
}

export interface WeKnoraListResponse<T> {
  items: T[];
  total: number;
  page?: number;
  page_size?: number;
  success?: boolean;
  message?: string;
}

export interface CreateWeKnoraKnowledgeBaseInput {
  name: string;
  description?: string;
}

export interface CreateWeKnoraManualKnowledgeInput {
  knowledgeBaseId: string;
  tenantId?: string | number;
  title: string;
  content: string;
}

export interface UploadWeKnoraKnowledgeFileInput {
  knowledgeBaseId: string;
  tenantId?: string | number;
  file: File;
}
