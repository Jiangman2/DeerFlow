"use client";

import { useRouter } from "next/navigation";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { brand } from "@/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { authClient } from "@/server/better-auth/client";

type Mode = "signIn" | "signUp";

export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = searchParams.get("next") ?? "/workspace";
  const [mode, setMode] = useState<Mode>("signIn");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [allowSignup, setAllowSignup] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/public/auth-settings");
        if (!res.ok) return;
        const data = (await res.json()) as { allowSignup?: boolean };
        if (!cancelled && typeof data.allowSignup === "boolean") {
          setAllowSignup(data.allowSignup);
        }
      } catch {
        return;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const passwordError = useMemo(() => {
    if (!password) return null;
    if (password.length < 8) return "密码至少 8 位";
    if (password.length > 128) return "密码最多 128 位";
    return null;
  }, [password]);

  const confirmError = useMemo(() => {
    if (mode !== "signUp") return null;
    if (!confirmPassword) return null;
    if (confirmPassword !== password) return "两次输入的密码不一致";
    return null;
  }, [confirmPassword, mode, password]);

  const canSubmit = useMemo(() => {
    if (!email.trim() || !password) return false;
    if (passwordError) return false;
    if (mode === "signUp" && !name.trim()) return false;
    if (mode === "signUp" && confirmPassword !== password) return false;
    return true;
  }, [confirmPassword, email, mode, name, password, passwordError]);

  async function onSubmit() {
    if (!canSubmit || loading) return;
    setLoading(true);
    setError(null);
    try {
      if (mode === "signIn") {
        const res = await authClient.signIn.email({
          email: email.trim(),
          password,
          rememberMe,
          callbackURL: nextPath,
        });
        if (res.error) {
          setError(res.error.message ?? "登录失败");
          return;
        }
        router.replace(nextPath);
        return;
      }

      const res = await authClient.signUp.email({
        name: name.trim(),
        email: email.trim(),
        password,
        callbackURL: nextPath,
      });
      if (res.error) {
        setError(res.error.message ?? "注册失败");
        return;
      }
      router.replace(nextPath);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="w-full space-y-6">
      <div className="space-y-1">
        <div className="text-2xl font-semibold">
          {mode === "signIn" ? "登录" : "注册"}
        </div>
        <div className="text-muted-foreground text-sm">
          {mode === "signIn"
            ? "使用邮箱密码登录"
            : "创建一个新账号（邮箱密码）"}
        </div>
        <div className="text-muted-foreground text-xs">
          {brand.productName}
        </div>
      </div>

      <div className="space-y-4">
        {mode === "signUp" && (
          <Input
            placeholder="昵称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="name"
          />
        )}
        <Input
          placeholder="邮箱"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          inputMode="email"
        />
        <Input
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          type="password"
          autoComplete={mode === "signIn" ? "current-password" : "new-password"}
          onKeyDown={(e) => {
            if (e.key === "Enter") void onSubmit();
          }}
        />
        {passwordError && <div className="text-sm text-red-600">{passwordError}</div>}

        {mode === "signUp" && (
          <>
            <Input
              placeholder="确认密码"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              type="password"
              autoComplete="new-password"
              onKeyDown={(e) => {
                if (e.key === "Enter") void onSubmit();
              }}
            />
            {confirmError && <div className="text-sm text-red-600">{confirmError}</div>}
          </>
        )}

        {mode === "signIn" && (
          <label className="flex select-none items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
            />
            记住登录状态
          </label>
        )}

        {error && <div className="text-sm text-red-600">{error}</div>}

        <Button className="w-full" disabled={!canSubmit || loading} onClick={onSubmit}>
          {loading ? "处理中…" : mode === "signIn" ? "登录" : "注册并登录"}
        </Button>

        <div className="text-muted-foreground text-sm">
          {mode === "signIn" ? "没有账号？" : "已有账号？"}{" "}
          <button
            type="button"
            className="text-foreground underline underline-offset-4"
            onClick={() => {
              if (mode === "signIn" && !allowSignup) {
                setError("管理员已关闭注册");
                return;
              }
              setError(null);
              setMode(mode === "signIn" ? "signUp" : "signIn");
            }}
          >
            {mode === "signIn" ? "去注册" : "去登录"}
          </button>
        </div>
      </div>
    </div>
  );
}
