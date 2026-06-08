"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";

function validateNextParam(next: string | null): string | null {
  if (!next) return null;
  if (!next.startsWith("/")) return null;
  if (next.startsWith("//")) return null;
  if (next.includes(":") && !next.startsWith("/")) return null;
  return next;
}

export default function LoginPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isLogin, setIsLogin] = useState(true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const redirectPath = validateNextParam(searchParams.get("next")) ?? "/workspace";

  useEffect(() => {
    if (isAuthenticated) {
      router.push(redirectPath);
    }
  }, [isAuthenticated, redirectPath, router]);

  useEffect(() => {
    let cancelled = false;

    void fetch("/api/v1/auth/setup-status")
      .then((r) => r.json())
      .then((data: { needs_setup?: boolean }) => {
        if (!cancelled && data.needs_setup) {
          router.push("/setup");
        }
      })
      .catch(() => {
        // Stay on the login page if setup status is temporarily unavailable.
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const endpoint = isLogin
        ? "/api/v1/auth/login/local"
        : "/api/v1/auth/register";
      const body = isLogin
        ? `username=${encodeURIComponent(email)}&password=${encodeURIComponent(password)}`
        : JSON.stringify({ email, password });
      const headers: HeadersInit = isLogin
        ? { "Content-Type": "application/x-www-form-urlencoded" }
        : { "Content-Type": "application/json" };

      const res = await fetch(endpoint, {
        method: "POST",
        headers,
        body,
        credentials: "include",
      });

      if (!res.ok) {
        const data = await res.json();
        setError(parseAuthError(data).message);
        return;
      }

      router.push(redirectPath);
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[#eef7ff] px-4">
      <div className="absolute inset-0 bg-[url('/images/home-bg-light.png')] bg-cover bg-center opacity-35" />
      <div className="absolute inset-0 bg-white/55" />
      <div className="relative z-10 w-full max-w-md space-y-6 rounded-lg border border-[#bfdbfe] bg-white/90 p-8 shadow-[0_24px_80px_rgba(15,42,85,0.18)] backdrop-blur">
        <div className="flex flex-col items-center text-center">
          <BrandLogo variant="full" priority className="h-12 w-auto" />
          <h1 className="mt-5 text-xl font-semibold text-slate-950">
            {brand.shortName} 账号登录
          </h1>
          <p className="mt-2 text-sm text-slate-500">
            {isLogin ? "登录后进入工作台" : "创建新的平台账号"}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="flex flex-col space-y-1">
            <label htmlFor="email" className="text-sm font-medium">
              Email
            </label>
            <Input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              required
            />
          </div>
          <div className="flex flex-col space-y-1">
            <label htmlFor="password" className="text-sm font-medium">
              Password
            </label>
            <Input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              required
              minLength={isLogin ? 6 : 8}
            />
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "Please wait..." : isLogin ? "Sign In" : "Create Account"}
          </Button>
        </form>

        <div className="text-center text-sm">
          <button
            type="button"
            onClick={() => {
              setIsLogin(!isLogin);
              setError("");
            }}
            className="text-blue-600 hover:underline"
          >
            {isLogin
              ? "Don't have an account? Sign up"
              : "Already have an account? Sign in"}
          </button>
        </div>

        <div className="text-center text-xs text-slate-500">
          <Link href={brand.links.home} className="hover:underline">
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
