import crypto from "node:crypto";

import { NextResponse } from "next/server";

import {
  countAdmins,
  deleteUserAndData,
  getUserFlags,
  isAdminUser,
  revokeUserSessions,
  setCredentialPassword,
  setUserFlags,
  updateUserIdentity,
} from "@/server/admin/db";
import { ensureAuthMigrations } from "@/server/better-auth";
import { getSession } from "@/server/better-auth/server";

export async function PATCH(request: Request, { params }: { params: Promise<{ userId: string }> }) {
  await ensureAuthMigrations();
  const s = await getSession();
  const actorId = s?.user?.id;
  if (!actorId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  if (!isAdminUser(actorId)) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  const { userId } = await params;
  const payload = (await request.json().catch(() => null)) as
    | {
        isAdmin?: boolean;
        isDisabled?: boolean;
        name?: string;
        email?: string;
        newPassword?: string;
        resetPassword?: boolean;
      }
    | null;
  if (!payload) return NextResponse.json({ detail: "invalid payload" }, { status: 400 });

  if (userId === actorId && payload.isDisabled === true) {
    return NextResponse.json({ detail: "cannot disable self" }, { status: 400 });
  }
  if (userId === actorId && payload.isAdmin === false) {
    return NextResponse.json({ detail: "cannot revoke self admin" }, { status: 400 });
  }

  const targetFlags = getUserFlags(userId);
  if (payload.isAdmin === false && targetFlags.isAdmin && countAdmins() <= 1) {
    return NextResponse.json({ detail: "cannot revoke last admin" }, { status: 400 });
  }
  if (payload.isDisabled === true && targetFlags.isAdmin && countAdmins() <= 1) {
    return NextResponse.json({ detail: "cannot disable last admin" }, { status: 400 });
  }

  if (typeof payload.name === "string" || typeof payload.email === "string") {
    try {
      updateUserIdentity(userId, { name: payload.name, email: payload.email });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "invalid user update";
      return NextResponse.json({ detail: msg }, { status: 400 });
    }
  }

  setUserFlags(userId, { isAdmin: payload.isAdmin, isDisabled: payload.isDisabled });

  let tempPassword: string | null = null;
  if (typeof payload.newPassword === "string") {
    const p = payload.newPassword;
    if (p.length < 8 || p.length > 128) {
      return NextResponse.json({ detail: "invalid password" }, { status: 400 });
    }
    try {
      setCredentialPassword(userId, p);
      revokeUserSessions(userId);
    } catch {
      return NextResponse.json({ detail: "set password failed" }, { status: 500 });
    }
  }
  if (payload.resetPassword) {
    const a = crypto.randomBytes(6).toString("base64url");
    const b = crypto.randomBytes(6).toString("base64url");
    tempPassword = `Tmp${a}!${b}`;
    try {
      setCredentialPassword(userId, tempPassword);
      revokeUserSessions(userId);
    } catch {
      return NextResponse.json({ detail: "reset password failed" }, { status: 500 });
    }
  }

  if (payload.isDisabled === true) revokeUserSessions(userId);
  return NextResponse.json({ success: true, tempPassword });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ userId: string }> }) {
  await ensureAuthMigrations();
  const s = await getSession();
  const actorId = s?.user?.id;
  if (!actorId) return NextResponse.json({ detail: "unauthorized" }, { status: 401 });
  if (!isAdminUser(actorId)) return NextResponse.json({ detail: "forbidden" }, { status: 403 });

  const { userId } = await params;
  if (userId === actorId) return NextResponse.json({ detail: "cannot delete self" }, { status: 400 });

  const targetFlags = getUserFlags(userId);
  if (targetFlags.isAdmin && countAdmins() <= 1) {
    return NextResponse.json({ detail: "cannot delete last admin" }, { status: 400 });
  }

  deleteUserAndData(userId);
  return NextResponse.json({ success: true });
}
