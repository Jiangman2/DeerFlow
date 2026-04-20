import { redirect } from "next/navigation";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { getSession } from "@/server/better-auth/server";

import LoginForm from "./login-form";

export default async function LoginPage() {
  const s = await getSession();
  if (s?.user) redirect("/workspace");

  return (
    <div className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex flex-col items-center gap-3">
          <BrandLogo variant="mark" priority className="h-10 w-auto" />
          <div className="text-center">
            <div className="text-slate-900 text-lg font-semibold">
              {brand.productName}
            </div>
            <div className="text-muted-foreground text-sm">
              {brand.organizationName}
            </div>
          </div>
        </div>
        <div className="bg-card text-card-foreground border-border rounded-2xl border p-6 shadow-[0_22px_80px_rgba(15,42,85,0.14)]">
          <LoginForm />
        </div>
      </div>
    </div>
  );
}
