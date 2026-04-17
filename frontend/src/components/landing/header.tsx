import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { getSession } from "@/server/better-auth/server";

export async function Header() {
  const s =
    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? null : await getSession();
  const href = s?.user ? brand.links.workspace : brand.links.login;
  const label = s?.user ? "进入工作台" : "登录";
  return (
    <header className="fixed top-0 right-0 left-0 z-20 w-full border-b border-border/70 bg-card/55 supports-[backdrop-filter]:backdrop-blur-md">
      <div className="container-md mx-auto flex h-16 items-center justify-between px-6">
        <div className="flex items-center gap-3">
          <BrandLogo variant="mark" priority className="h-11 w-auto md:h-14" />
        </div>
        {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ? (
          <Button size="sm" asChild>
            <Link href={href}>{label}</Link>
          </Button>
        ) : (
          <Button size="sm" asChild>
            <Link href={href}>{label}</Link>
          </Button>
        )}
      </div>
    </header>
  );
}
