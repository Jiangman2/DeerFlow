"use client";

import { LogOutIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { fetch, getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { useI18n } from "@/core/i18n/hooks";

import { AccountUsageCard } from "./account-usage-card";
import { SettingsSection } from "./settings-section";

export function AccountSettingsPage() {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const isSsoUser = Boolean(user?.oauth_provider);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setMessage("");

    if (newPassword !== confirmPassword) {
      setError(t.settings.account.passwordMismatch);
      return;
    }
    if (newPassword.length < 8) {
      setError(t.settings.account.passwordTooShort);
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getCsrfHeaders(),
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      setMessage(t.settings.account.passwordChangedSuccess);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    } catch {
      setError(t.settings.account.networkError);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <SettingsSection title={t.settings.account.profileTitle}>
        <div className="space-y-2">
          <div className="grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] items-center gap-4">
            <span className="text-muted-foreground text-sm">
              {t.settings.account.displayName}
            </span>
            <span className="text-sm font-medium break-words">
              {user?.display_name?.trim() ? user.display_name : "—"}
            </span>
            {user?.organization_name ? (
              <>
                <span className="text-muted-foreground text-sm">
                  {t.settings.account.organization}
                </span>
                <span className="text-sm font-medium break-words">
                  {user.organization_name}
                </span>
              </>
            ) : null}
            <span className="text-muted-foreground text-sm">
              {t.settings.account.email}
            </span>
            <span className="text-sm font-medium break-all">
              {user?.email ?? "—"}
            </span>
            <span className="text-muted-foreground text-sm">
              {t.settings.account.role}
            </span>
            <span className="text-sm font-medium capitalize">
              {user?.system_role === "admin"
                ? t.settings.account.adminRole
                : user
                  ? t.settings.account.userRole
                  : "—"}
            </span>
            {isSsoUser && (
              <>
                <span className="text-muted-foreground text-sm">
                  {t.settings.account.ssoProvider}
                </span>
                <span className="text-sm font-medium capitalize">
                  {user?.oauth_provider}
                </span>
              </>
            )}
          </div>
        </div>
      </SettingsSection>
      <SettingsSection title={t.settings.account.usageTitle}>
        <AccountUsageCard />
      </SettingsSection>

      {!isSsoUser ? (
        <SettingsSection title={t.settings.account.changePasswordTitle}>
          <form onSubmit={handleChangePassword} className="max-w-sm space-y-3">
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t.settings.account.currentPassword}</span>
              <Input
                type="password"
                autoComplete="current-password"
                placeholder={t.settings.account.currentPassword}
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                required
              />
            </label>
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t.settings.account.newPassword}</span>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={t.settings.account.newPassword}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
              />
            </label>
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t.settings.account.confirmNewPassword}</span>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={t.settings.account.confirmNewPassword}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            )}
            {message && (
              <p role="status" className="text-sm text-green-700">
                {message}
              </p>
            )}
            <Button
              type="submit"
              variant="outline"
              size="sm"
              disabled={loading}
            >
              {loading
                ? t.settings.account.updating
                : t.settings.account.updatePassword}
            </Button>
          </form>
        </SettingsSection>
      ) : (
        <SettingsSection
          title={t.settings.account.changePasswordTitle}
          description={t.settings.account.ssoPasswordDescription}
        >
          <p className="text-muted-foreground text-sm">
            {t.settings.account.ssoPasswordMessage.replace(
              "{provider}",
              user?.oauth_provider ?? "",
            )}
          </p>
        </SettingsSection>
      )}

      <SettingsSection title="" description="">
        <Button
          variant="destructive"
          size="sm"
          onClick={logout}
          className="gap-2"
        >
          <LogOutIcon className="size-4" />
          {t.settings.account.signOut}
        </Button>
      </SettingsSection>
    </div>
  );
}
