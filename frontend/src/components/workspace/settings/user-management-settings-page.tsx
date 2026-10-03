"use client";

import {
  KeyRoundIcon,
  LoaderCircleIcon,
  PlusIcon,
  RefreshCcwIcon,
  SearchIcon,
  ShieldCheckIcon,
  Trash2Icon,
  UserRoundIcon,
} from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  useCreateManagedUser,
  useDeleteManagedUser,
  useManagedUsers,
  useResetManagedUserPassword,
  useUpdateManagedUser,
} from "@/core/admin-users";
import type {
  ManagedUser,
  ManagedUserRole,
  UpdateUserInput,
} from "@/core/admin-users";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

type CreateForm = {
  email: string;
  password: string;
  system_role: ManagedUserRole;
  needs_setup: boolean;
};

const emptyCreateForm: CreateForm = {
  email: "",
  password: "",
  system_role: "user",
  needs_setup: true,
};

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function TemporaryPasswordNotice({
  password,
  onClear,
}: {
  password: string | null;
  onClear: () => void;
}) {
  if (!password) return null;
  return (
    <div className="rounded-md border bg-muted/40 p-3 text-sm">
      <div className="font-medium">临时密码只会显示这一次</div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <code className="rounded bg-background px-2 py-1 text-xs break-all">
          {password}
        </code>
        <Button type="button" size="sm" variant="outline" onClick={onClear}>
          已记录
        </Button>
      </div>
    </div>
  );
}

function UserRow({
  item,
  isCurrentUser,
  busy,
  onRoleChange,
  onDisabledChange,
  onResetPassword,
  onDelete,
}: {
  item: ManagedUser;
  isCurrentUser: boolean;
  busy: boolean;
  onRoleChange: (role: ManagedUserRole) => void;
  onDisabledChange: (disabled: boolean) => void;
  onResetPassword: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="grid gap-3 rounded-md border p-3 text-sm lg:grid-cols-[minmax(0,1fr)_150px_170px_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          {item.system_role === "admin" ? (
            <ShieldCheckIcon className="text-primary size-4" />
          ) : (
            <UserRoundIcon className="text-muted-foreground size-4" />
          )}
          <span className="font-medium break-all">{item.email}</span>
          {isCurrentUser ? <Badge variant="secondary">当前账号</Badge> : null}
          {item.is_disabled ? <Badge variant="destructive">已禁用</Badge> : null}
          {item.oauth_provider ? (
            <Badge variant="outline">{item.oauth_provider}</Badge>
          ) : null}
        </div>
        <div className="text-muted-foreground mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
          <span>ID: {item.id}</span>
          <span>创建: {formatDate(item.created_at)}</span>
          <span>对话: {item.thread_count}</span>
          <span>任务: {item.run_count}</span>
          <span>令牌版本: {item.token_version}</span>
        </div>
      </div>

      <Select
        value={item.system_role}
        disabled={busy || isCurrentUser}
        onValueChange={(value) => onRoleChange(value as ManagedUserRole)}
      >
        <SelectTrigger className="w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="user">用户</SelectItem>
          <SelectItem value="admin">管理员</SelectItem>
        </SelectContent>
      </Select>

      <label className="flex items-center justify-between gap-3 rounded-md border px-3 py-2">
        <span className="text-sm font-medium">禁用账号</span>
        <Switch
          checked={item.is_disabled}
          disabled={busy || isCurrentUser}
          onCheckedChange={onDisabledChange}
        />
      </label>

      <div className="flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={onResetPassword}
        >
          <KeyRoundIcon />
          重置
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy || isCurrentUser}
          onClick={onDelete}
        >
          <Trash2Icon />
          删除
        </Button>
      </div>
    </div>
  );
}

export function UserManagementSettingsPage() {
  const { user: currentUser } = useAuth();
  const [query, setQuery] = useState("");
  const [form, setForm] = useState<CreateForm>(emptyCreateForm);
  const [deleteFileData, setDeleteFileData] = useState(false);
  const [temporaryPassword, setTemporaryPassword] = useState<string | null>(
    null,
  );
  const usersQuery = useManagedUsers(query);
  const createMutation = useCreateManagedUser();
  const updateMutation = useUpdateManagedUser();
  const resetMutation = useResetManagedUserPassword();
  const deleteMutation = useDeleteManagedUser();
  const busy =
    createMutation.isPending ||
    updateMutation.isPending ||
    resetMutation.isPending ||
    deleteMutation.isPending;

  const total = usersQuery.data?.total ?? 0;
  const currentUserId = currentUser?.id;
  const sortedUsers = useMemo(() => {
    const users = usersQuery.data?.items ?? [];
    return [...users].sort((a, b) => {
        if (a.id === currentUserId) return -1;
        if (b.id === currentUserId) return 1;
        return a.email.localeCompare(b.email);
      });
  }, [currentUserId, usersQuery.data?.items]);

  const createUser = async () => {
    const email = form.email.trim();
    if (!email) {
      toast.error("请输入邮箱");
      return;
    }
    const result = await createMutation.mutateAsync({
      email,
      password: form.password.trim() || undefined,
      system_role: form.system_role,
      needs_setup: form.needs_setup,
    });
    setTemporaryPassword(result.temporary_password ?? null);
    setForm(emptyCreateForm);
    toast.success("用户已创建");
  };

  const updateUser = async (item: ManagedUser, input: UpdateUserInput) => {
    await updateMutation.mutateAsync({ userId: item.id, input });
    toast.success("用户已更新");
  };

  const resetPassword = async (item: ManagedUser) => {
    if (!window.confirm(`为 ${item.email} 生成新的临时密码？`)) return;
    const result = await resetMutation.mutateAsync({
      userId: item.id,
      input: { needs_setup: true },
    });
    setTemporaryPassword(result.temporary_password ?? null);
    toast.success("密码已重置");
  };

  const deleteUser = async (item: ManagedUser) => {
    const suffix = deleteFileData ? "，并删除该用户在 PVC 中的文件数据" : "";
    if (!window.confirm(`确认删除 ${item.email}${suffix}？`)) return;
    await deleteMutation.mutateAsync({
      userId: item.id,
      deleteFileData,
    });
    toast.success("用户已删除");
  };

  return (
    <SettingsSection
      title="用户管理"
      description="管理员可以创建用户、分配角色、禁用账号、重置密码和清理用户数据。"
    >
      <div className="space-y-6">
        <TemporaryPasswordNotice
          password={temporaryPassword}
          onClear={() => setTemporaryPassword(null)}
        />

        <form
          className="grid gap-3 rounded-md border p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_140px_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            void createUser();
          }}
        >
          <Input
            type="email"
            placeholder="邮箱"
            value={form.email}
            onChange={(event) =>
              setForm((current) => ({ ...current, email: event.target.value }))
            }
          />
          <Input
            type="password"
            placeholder="初始密码，留空自动生成"
            value={form.password}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                password: event.target.value,
              }))
            }
          />
          <Select
            value={form.system_role}
            onValueChange={(value) =>
              setForm((current) => ({
                ...current,
                system_role: value as ManagedUserRole,
              }))
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="user">用户</SelectItem>
              <SelectItem value="admin">管理员</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" disabled={busy}>
            {createMutation.isPending ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <PlusIcon />
            )}
            创建
          </Button>
        </form>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative min-w-64 flex-1">
            <SearchIcon className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
            <Input
              className="pl-9"
              placeholder="搜索邮箱"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={deleteFileData}
              onCheckedChange={setDeleteFileData}
            />
            删除用户时同步删除 PVC 文件数据
          </label>
          <Button
            type="button"
            variant="outline"
            disabled={usersQuery.isFetching}
            onClick={() => void usersQuery.refetch()}
          >
            <RefreshCcwIcon
              className={cn(usersQuery.isFetching && "animate-spin")}
            />
            刷新
          </Button>
        </div>

        <div className="space-y-2">
          <div className="text-muted-foreground text-sm">
            共 {total} 个用户
          </div>
          {usersQuery.isLoading ? (
            <div className="text-muted-foreground flex items-center gap-2 rounded-md border border-dashed p-4 text-sm">
              <LoaderCircleIcon className="size-4 animate-spin" />
              加载中
            </div>
          ) : sortedUsers.length === 0 ? (
            <div className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
              没有匹配的用户
            </div>
          ) : (
            sortedUsers.map((item) => (
              <UserRow
                key={item.id}
                item={item}
                isCurrentUser={item.id === currentUserId}
                busy={busy}
                onRoleChange={(role) =>
                  void updateUser(item, { system_role: role })
                }
                onDisabledChange={(isDisabled) =>
                  void updateUser(item, { is_disabled: isDisabled })
                }
                onResetPassword={() => void resetPassword(item)}
                onDelete={() => void deleteUser(item)}
              />
            ))
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
