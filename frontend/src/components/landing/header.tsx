import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import type { Locale } from "@/core/i18n/locale";
import { cn } from "@/lib/utils";

import { MobileNav } from "./mobile-nav";

export type HeaderProps = {
  className?: string;
  homeURL?: string;
  locale?: Locale;
};

export function Header({ className, homeURL }: HeaderProps) {
  return (
    <header
      className={cn(
        "container-md fixed top-0 right-0 left-0 z-20 mx-auto flex h-16 items-center justify-between gap-3 px-4 backdrop-blur-xs",
        className,
      )}
    >
      <Link
        href={homeURL ?? brand.links.home}
        className="flex min-w-0 items-center"
      >
        <BrandLogo variant="full" priority className="h-9 w-auto md:h-10" />
      </Link>
      <div className="ml-auto hidden items-center gap-2 sm:flex">
        <Button variant="ghost" size="sm" asChild>
          <Link href={brand.links.login}>登录</Link>
        </Button>
        <Button size="sm" asChild>
          <Link href={brand.links.workspace}>进入工作台</Link>
        </Button>
      </div>
      <MobileNav
        links={[
          { href: brand.links.login, label: "登录" },
          { href: brand.links.workspace, label: "进入工作台" },
        ]}
      />
      <hr className="from-border/0 via-border/70 to-border/0 absolute top-16 right-0 left-0 z-10 m-0 h-px w-full border-none bg-linear-to-r" />
    </header>
  );
}
