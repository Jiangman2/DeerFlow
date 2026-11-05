"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getCsrfHeaders } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import {
  fetchSetupStatus,
  isSystemAlreadyInitializedError,
} from "@/core/auth/setup";
import { parseAuthError } from "@/core/auth/types";

type SetupMode = "loading" | "init_admin" | "change_password";

export default function SetupPage() {
  const router = useRouter();
  const { user, isAuthenticated } = useAuth();
  const [mode, setMode] = useState<SetupMode>("loading");

  // --- Shared state ---
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // --- Change-password mode only ---
  const [currentPassword, setCurrentPassword] = useState("");

  useEffect(() => {
    let cancelled = false;

    if (isAuthenticated && user?.needs_setup) {
      setMode("change_password");
    } else if (!isAuthenticated) {
      // Check if the system has no users yet
      void fetchSetupStatus()
        .then((data: { needs_setup?: boolean }) => {
          if (cancelled) return;
          if (data.needs_setup) {
            setMode("init_admin");
          } else {
            // System already set up and user is not logged in — go to login
            router.replace("/login");
          }
        })
        .catch(() => {
          if (!cancelled) router.replace("/login");
        });
    } else {
      // Authenticated but needs_setup is false — already set up
      router.replace("/workspace");
    }

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user, router]);

  // ── Init-admin handler ─────────────────────────────────────────────
  const handleInitAdmin = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/initialize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email,
          password: newPassword,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        if (isSystemAlreadyInitializedError(data)) {
          router.replace("/login");
          return;
        }
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // ── Change-password handler ────────────────────────────────────────
  const handleChangePassword = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters");
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
        credentials: "include",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
          new_email: email || undefined,
        }),
      });

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  if (mode === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">Loading…</p>
      </div>
    );
  }

  // ── Admin initialization form ──────────────────────────────────────
  if (mode === "init_admin") {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <div className="absolute inset-0 z-0 bg-[url('/images/workspace-bg-light.png')] bg-cover bg-center bg-no-repeat opacity-70" />
        <div className="bg-background/70 absolute inset-0 z-0 backdrop-blur-sm" />
        <div className="border-border/20 bg-background/5 relative z-10 w-full max-w-md space-y-5 rounded-3xl border p-6 backdrop-blur-sm sm:space-y-6 sm:p-8">
          <div className="flex flex-col items-center text-center">
            <BrandLogo variant="full" priority className="h-12 w-auto" />
            <h1 className="text-foreground mt-5 text-xl font-semibold">
              {brand.shortName}
            </h1>
            <p className="text-muted-foreground mt-2">创建管理员账号</p>
            <p className="text-muted-foreground mt-1 text-xs">
              首次使用前需要先完成管理员初始化。
            </p>
          </div>
          <form onSubmit={handleInitAdmin} className="space-y-3">
            <div className="flex flex-col space-y-1">
              <label htmlFor="email" className="text-sm font-medium">
                邮箱
              </label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="password" className="text-sm font-medium">
                密码
              </label>
              <Input
                id="password"
                type="password"
                placeholder="Password (min. 8 characters)"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirmPassword" className="text-sm font-medium">
                确认密码
              </label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="Confirm password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
              />
            </div>
            {error && <p className="ms-1 text-sm text-red-500">{error}</p>}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? "正在创建..." : "创建管理员账号"}
            </Button>
          </form>
        </div>
      </div>
    );
  }

  // ── Change-password form (needs_setup after login) ─────────────────
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">
      <div className="absolute inset-0 z-0 bg-[url('/images/workspace-bg-light.png')] bg-cover bg-center bg-no-repeat opacity-70" />
      <div className="bg-background/70 absolute inset-0 z-0 backdrop-blur-sm" />
      <div className="border-border/20 bg-background/5 relative z-10 w-full max-w-md space-y-5 rounded-3xl border p-6 backdrop-blur-sm sm:space-y-6 sm:p-8">
        <div className="flex flex-col items-center text-center">
          <BrandLogo variant="full" priority className="h-12 w-auto" />
          <h1 className="text-foreground mt-5 text-xl font-semibold">
            {brand.shortName}
          </h1>
          <p className="text-muted-foreground mt-2">完成管理员账号设置</p>
          <p className="text-muted-foreground mt-1 text-xs">
            请设置真实邮箱和新密码。
          </p>
        </div>
        <form onSubmit={handleChangePassword} className="space-y-3">
          <Input
            type="email"
            placeholder="邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder="当前密码"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
          <Input
            type="password"
            placeholder="新密码"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
            minLength={8}
          />
          <Input
            type="password"
            placeholder="确认新密码"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            required
            minLength={8}
          />
          {error && <p className="text-sm text-red-500">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "正在设置..." : "完成设置"}
          </Button>
        </form>
      </div>
    </div>
  );
}
