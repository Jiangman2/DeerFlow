import Link from "next/link";

import { brand } from "@/brand";
import { BrandLogo } from "@/components/brand/brand-logo";
import { Button } from "@/components/ui/button";
import type { Locale } from "@/core/i18n/locale";
import { getI18n } from "@/core/i18n/server";
import { cn } from "@/lib/utils";

import { MobileNav } from "./mobile-nav";

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
          className="flex min-w-0 items-center font-serif text-xl whitespace-nowrap"
        >
          <BrandLogo
            variant="full"
            priority
            className="h-7 w-auto max-w-[13rem] sm:h-8 sm:max-w-none"
          />
        </a>
      </div>
      <nav className="ml-auto hidden items-center gap-3 text-sm font-medium sm:flex">
        <Button
          variant="outline"
          size="sm"
          className="h-9 rounded-lg border-[#0868bd]/35 bg-white/80 px-4 text-[#0868bd] shadow-sm hover:border-[#0868bd]/60 hover:bg-[#eef7ff] hover:text-[#07569f] focus-visible:ring-[#0868bd]/25"
          asChild
        >
          <Link href={brand.links.login}>{t.login.signIn}</Link>
        </Button>
        <Button
          size="sm"
          className="h-9 rounded-lg border border-[#07569f] bg-[#0868bd] px-4 text-white shadow-[0_5px_14px_rgba(8,104,189,0.22)] hover:-translate-y-px hover:bg-[#075aa5] hover:shadow-[0_7px_18px_rgba(8,104,189,0.28)] active:translate-y-0 active:bg-[#064c8c] focus-visible:ring-[#0868bd]/30"
          asChild
        >
          <Link href={brand.links.login}>{t.sidebar.newChat}</Link>
        </Button>
      </nav>
      <MobileNav
        links={[
          { href: brand.links.login, label: t.login.signIn },
          { href: brand.links.login, label: t.sidebar.newChat },
        ]}
      />
      <hr className="from-border/0 via-border/45 to-border/0 absolute right-0 bottom-0 left-0 z-10 m-0 h-px w-full border-none bg-linear-to-r" />
    </header>
  );
}
