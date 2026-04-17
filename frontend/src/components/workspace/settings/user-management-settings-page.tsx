"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Switch } from "@/components/ui/switch";

type ManagedUser = {
  id: string;
  name: string;
  email: string;
  emailVerified: boolean;
  createdAt: string;
  updatedAt: string;
  isAdmin: boolean;
  isDisabled: boolean;
};

export function UserManagementSettingsPage() {
  const [loading, setLoading] = useState(true);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [allowSignup, setAllowSignup] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [meId, setMeId] = useState<string | null>(null);
  const [editUser, setEditUser] = useState<ManagedUser | null>(null);
  const [editName, setEditName] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [confirm, setConfirm] = useState<{
    kind: "delete" | "reset";
    user: ManagedUser;
  } | null>(null);
  const [passwordDialog, setPasswordDialog] = useState<{
    user: ManagedUser;
    tempPassword: string;
  } | null>(null);
  const [setPwdUser, setSetPwdUser] = useState<ManagedUser | null>(null);
  const [setPwd, setSetPwd] = useState("");
  const [setPwdConfirm, setSetPwdConfirm] = useState("");
  const [setPwdOpen, setSetPwdOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [usersRes, settingsRes] = await Promise.all([
        fetch("/api/admin/users", { cache: "no-store" }),
        fetch("/api/admin/settings", { cache: "no-store" }),
      ]);
      if (!usersRes.ok) {
        const t = await usersRes.text();
        throw new Error(t ? t : "failed to load users");
      }
      if (!settingsRes.ok) {
        const t = await settingsRes.text();
        throw new Error(t ? t : "failed to load settings");
      }
      const usersJson = (await usersRes.json()) as { users: ManagedUser[] };
      const settingsJson = (await settingsRes.json()) as { allowSignup: boolean };
      setUsers(usersJson.users ?? []);
      setAllowSignup(settingsJson.allowSignup);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetch("/api/admin/users/me", { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) return;
        const data = (await r.json().catch(() => null)) as
          | { user?: { id?: string } }
          | null;
        const id = data?.user?.id;
        if (typeof id === "string" && id) setMeId(id);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const updateUser = useCallback(
    async (
      userId: string,
      patch: {
        isAdmin?: boolean;
        isDisabled?: boolean;
        name?: string;
        email?: string;
        newPassword?: string;
        resetPassword?: boolean;
      },
    ) => {
      setError(null);
      const res = await fetch(`/api/admin/users/${userId}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t ? t : "更新失败");
      }
      const body = (await res.json().catch(() => null)) as { tempPassword?: string } | null;
      const wouldLogoutMe =
        userId === meId &&
        [patch.isDisabled, patch.resetPassword, patch.newPassword].some((v) => v === true);
      if (wouldLogoutMe && patch.resetPassword) {
        return body?.tempPassword ?? null;
      }
      if (wouldLogoutMe && typeof patch.newPassword === "string") {
        window.location.href = "/login";
        return null;
      }
      if (wouldLogoutMe) {
        window.location.href = "/login";
        return body?.tempPassword ?? null;
      }
      await refresh();
      return body?.tempPassword ?? null;
    },
    [meId, refresh],
  );

  const deleteUser = useCallback(
    async (userId: string) => {
      setError(null);
      const res = await fetch(`/api/admin/users/${userId}`, { method: "DELETE" });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t ? t : "删除失败");
      }
      await refresh();
    },
    [refresh],
  );

  const toggleSignup = useCallback(
    async (next: boolean) => {
      setError(null);
      const res = await fetch("/api/admin/settings", {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ allowSignup: next }),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t ? t : "更新失败");
      }
      const data = (await res.json()) as { allowSignup?: boolean };
      if (typeof data.allowSignup === "boolean") setAllowSignup(data.allowSignup);
    },
    [],
  );

  const openEdit = useCallback((u: ManagedUser) => {
    setEditUser(u);
    setEditName(u.name ?? "");
    setEditEmail(u.email ?? "");
    setEditOpen(true);
  }, []);

  const submitEdit = useCallback(async () => {
    if (!editUser) return;
    const namePatch = editName.trim() ? editName : undefined;
    const emailPatch = editEmail.trim() ? editEmail : undefined;
    if (!namePatch && !emailPatch) {
      toast.message("未修改任何内容");
      setEditOpen(false);
      return;
    }
    try {
      await updateUser(editUser.id, { name: namePatch, email: emailPatch });
      toast.success("已更新");
      setEditOpen(false);
    } catch (e) {
      setError(String(e));
    }
  }, [editEmail, editName, editUser, updateUser]);

  const confirmAction = useCallback(async () => {
    if (!confirm) return;
    const u = confirm.user;
    const kind = confirm.kind;
    setConfirm(null);
    if (kind === "delete") {
      try {
        await deleteUser(u.id);
        toast.success("已删除");
      } catch (e) {
        setError(String(e));
      }
      return;
    }
    if (kind === "reset") {
      try {
        const p = await updateUser(u.id, { resetPassword: true });
        if (!p) {
          setError("重置失败：未返回临时密码");
          return;
        }
        setPasswordDialog({ user: u, tempPassword: p });
      } catch (e) {
        setError(String(e));
      }
    }
  }, [confirm, deleteUser, updateUser]);

  const copyText = useCallback(async (text: string) => {
    if (!text) return false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {
      // ignore
    }
    try {
      const el = document.createElement("textarea");
      el.value = text;
      el.setAttribute("readonly", "true");
      el.style.position = "fixed";
      el.style.top = "0";
      el.style.left = "0";
      el.style.width = "1px";
      el.style.height = "1px";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.focus();
      el.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(el);
      return ok;
    } catch {
      return false;
    }
  }, []);

  const openSetPassword = useCallback((u: ManagedUser) => {
    setSetPwdUser(u);
    setSetPwd("");
    setSetPwdConfirm("");
    setSetPwdOpen(true);
  }, []);

  const submitSetPassword = useCallback(async () => {
    if (!setPwdUser) return;
    if (setPwd.length < 8) {
      toast.message("密码至少 8 位");
      return;
    }
    if (setPwd !== setPwdConfirm) {
      toast.message("两次输入的密码不一致");
      return;
    }
    try {
      await updateUser(setPwdUser.id, { newPassword: setPwd });
      toast.success("密码已更新");
      setSetPwdOpen(false);
    } catch (e) {
      setError(String(e));
    }
  }, [setPwd, setPwdConfirm, setPwdUser, updateUser]);

  return (
    <div className="space-y-4">
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改资料</DialogTitle>
            <DialogDescription>
              {editUser ? (editUser.email ? editUser.email : editUser.id) : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <div className="text-sm">昵称</div>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)} />
            </div>
            <div className="space-y-1">
              <div className="text-sm">邮箱</div>
              <Input
                value={editEmail}
                onChange={(e) => setEditEmail(e.target.value)}
                placeholder="留空表示不修改"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void submitEdit()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirm !== null} onOpenChange={(o) => (o ? null : setConfirm(null))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {confirm?.kind === "delete" ? "确认删除用户" : "确认重置密码"}
            </DialogTitle>
            <DialogDescription>
              {confirm?.user ? (confirm.user.email ? confirm.user.email : confirm.user.id) : ""}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              取消
            </Button>
            <Button
              variant={confirm?.kind === "delete" ? "destructive" : "default"}
              onClick={() => void confirmAction()}
            >
              确认
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={passwordDialog !== null} onOpenChange={(o) => (o ? null : setPasswordDialog(null))}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>临时密码</DialogTitle>
            <DialogDescription>
              {passwordDialog?.user
                ? passwordDialog.user.email
                  ? passwordDialog.user.email
                  : passwordDialog.user.id
                : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Input readOnly value={passwordDialog?.tempPassword ?? ""} />
            <div className="text-muted-foreground text-sm">
              已重置密码并踢下线，请尽快交付临时密码并要求用户登录后修改。
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                const p = passwordDialog?.tempPassword ?? "";
                void copyText(p).then((ok) => {
                  if (ok) toast.success("已复制");
                  else toast.message("复制失败");
                });
              }}
            >
              复制
            </Button>
            <Button onClick={() => setPasswordDialog(null)}>关闭</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={setPwdOpen} onOpenChange={setSetPwdOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>设置密码</DialogTitle>
            <DialogDescription>
              {setPwdUser ? (setPwdUser.email ? setPwdUser.email : setPwdUser.id) : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <div className="text-sm">新密码</div>
              <Input
                type="password"
                value={setPwd}
                onChange={(e) => setSetPwd(e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <div className="text-sm">确认新密码</div>
              <Input
                type="password"
                value={setPwdConfirm}
                onChange={(e) => setSetPwdConfirm(e.target.value)}
              />
            </div>
            <div className="text-muted-foreground text-sm">
              设置后会让该用户下线，需要使用新密码重新登录。
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSetPwdOpen(false)}>
              取消
            </Button>
            <Button onClick={() => void submitSetPassword()}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ItemGroup>
        <Item variant="outline">
          <ItemContent>
            <ItemTitle>允许注册</ItemTitle>
            <ItemDescription>关闭后，登录页将无法注册新账号</ItemDescription>
          </ItemContent>
          <ItemActions>
            <Switch
              checked={allowSignup ?? true}
              disabled={allowSignup === null}
              onCheckedChange={(v) => void toggleSignup(v).catch((e) => setError(String(e)))}
            />
          </ItemActions>
        </Item>
      </ItemGroup>

      <div className="flex items-center justify-between">
        <div className="text-sm font-medium">用户列表</div>
        <Button variant="outline" size="sm" onClick={() => void refresh()}>
          刷新
        </Button>
      </div>

      {error && <div className="text-destructive text-sm">{error}</div>}

      <ItemGroup>
        {users.map((u, idx) => (
          <div key={u.id}>
            {idx > 0 && <ItemSeparator />}
            <Item variant="outline">
              <ItemContent>
                <ItemTitle className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs">
                    {u.email ? u.email : u.id}
                  </span>
                  {u.isAdmin && <span className="text-xs">管理员</span>}
                  {u.isDisabled && <span className="text-xs">已禁用</span>}
                  {u.emailVerified && <span className="text-xs">已验证</span>}
                </ItemTitle>
                <ItemDescription>
                  {u.name || "—"} · 创建于 {u.createdAt}
                </ItemDescription>
              </ItemContent>
              <ItemActions className="flex flex-wrap justify-end">
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground text-xs">管理员</span>
                  <Switch
                    checked={u.isAdmin}
                    onCheckedChange={(v) =>
                      void updateUser(u.id, { isAdmin: v })
                        .then(() => toast.success("已更新"))
                        .catch((e) => setError(String(e)))
                    }
                  />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground text-xs">禁用</span>
                  <Switch
                    checked={u.isDisabled}
                    onCheckedChange={(v) =>
                      void updateUser(u.id, { isDisabled: v })
                        .then(() => {
                          if (u.id === meId && v) toast.message("你已被禁用，将退出登录。");
                          else toast.success("已更新");
                        })
                        .catch((e) => setError(String(e)))
                    }
                  />
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    openEdit(u);
                  }}
                >
                  修改资料
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    openSetPassword(u);
                  }}
                >
                  设置密码
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setConfirm({ kind: "reset", user: u });
                  }}
                >
                  重置密码
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  disabled={u.id === meId}
                  onClick={() => {
                    if (u.id === meId) {
                      toast.message("不能删除当前登录用户");
                      return;
                    }
                    setConfirm({ kind: "delete", user: u });
                  }}
                >
                  删除
                </Button>
              </ItemActions>
            </Item>
          </div>
        ))}
      </ItemGroup>

      {loading && <div className="text-muted-foreground text-sm">加载中…</div>}
      {users.length === 0 && !loading && (
        <div className="text-muted-foreground text-sm">暂无用户</div>
      )}
    </div>
  );
}
