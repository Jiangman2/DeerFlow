export interface KnowledgeFile {
  id: string;
  filename: string;
  folder_path: string;
  display_path: string;
  original_filename: string;
  content_type: string | null;
  size: number;
  sha256: string;
  status: "indexed" | "failed" | string;
  error: string | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeFolder {
  id: string;
  name: string;
  path: string;
  parent_path: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeListResponse {
  files: KnowledgeFile[];
  folders: KnowledgeFolder[];
  count: number;
}

export interface KnowledgeUploadResponse {
  success: boolean;
  files: KnowledgeFile[];
  message: string;
}

export interface KnowledgeFolderDeleteResponse {
  success: boolean;
  deleted_count: number;
  message: string;
}

export interface KnowledgeFileMoveInput {
  fileId: string;
  target_folder_path?: string;
  target_filename?: string | null;
}

export interface KnowledgeFileCopyInput {
  fileId: string;
  target_folder_path?: string;
  target_filename?: string | null;
}

export interface KnowledgeFolderCreateInput {
  path: string;
}

export interface KnowledgeFolderRenameInput {
  folderPath: string;
  new_name: string;
}

export interface KnowledgeFolderMoveInput {
  folderPath: string;
  target_path: string;
}

export interface KnowledgeSearchResult {
  file_id: string;
  filename: string;
  chunk_id: string;
  chunk_index: number;
  heading: string | null;
  snippet: string;
  score: number;
}

export interface KnowledgeSearchResponse {
  results: KnowledgeSearchResult[];
  count: number;
}
