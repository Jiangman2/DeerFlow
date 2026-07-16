import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import type { Locale } from "@/core/i18n/locale";
import { getI18n } from "@/core/i18n/server";
import { cn } from "@/lib/utils";

export type HeaderProps = {
  className?: string;
  homeURL?: string;
  locale?: Locale;
};

export async function Header({ className, homeURL, locale }: HeaderProps) {
  const isExternalHome = false;
  const { t } = await getI18n(locale);
  return (
    <header
      className={cn(
        "container-md fixed top-0 right-0 left-0 z-20 mx-auto flex h-20 items-center justify-between gap-3 bg-white/45 px-5 backdrop-blur-md sm:px-8",
        className,
      )}
    >
      <div className="flex min-w-0 items-center gap-6">
        <a
          href={homeURL ?? brand.links.home}
          target={isExternalHome ? "_blank" : "_self"}
          rel={isExternalHome ? "noopener noreferrer" : undefined}
          className="font-serif text-xl whitespace-nowrap"
        >
          <BrandLogo variant="full" priority className="h-8 w-auto" />
        </a>
      </div>
      <nav className="ml-auto flex items-center gap-3 text-sm font-medium">
        <Button variant="outline" size="sm" asChild>
          <Link href={brand.links.login}>{t.login.signIn}</Link>
        </Button>
        <Button size="sm" asChild>
          <Link href={brand.links.workspace}>{t.sidebar.newChat}</Link>
        </Button>
      </nav>
      <hr className="from-border/0 via-border/45 to-border/0 absolute right-0 bottom-0 left-0 z-10 m-0 h-px w-full border-none bg-linear-to-r" />
    </header>
  );
}
