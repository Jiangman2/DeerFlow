export type ManagedUserRole = "admin" | "user";

export interface ManagedUser {
  id: string;
  email: string;
  system_role: ManagedUserRole;
  needs_setup: boolean;
  is_disabled: boolean;
  oauth_provider?: string | null;
  created_at: string;
  token_version: number;
  thread_count: number;
  run_count: number;
}

export interface UserListResponse {
  items: ManagedUser[];
  total: number;
}

export interface CreateUserInput {
  email: string;
  password?: string;
  system_role: ManagedUserRole;
  needs_setup: boolean;
}

export interface CreateUserResponse {
  item: ManagedUser;
  temporary_password?: string | null;
}

export interface UpdateUserInput {
  email?: string;
  system_role?: ManagedUserRole;
  needs_setup?: boolean;
  is_disabled?: boolean;
}

export interface ResetPasswordInput {
  new_password?: string;
  needs_setup?: boolean;
}

export interface ResetPasswordResponse {
  item: ManagedUser;
  temporary_password?: string | null;
}
