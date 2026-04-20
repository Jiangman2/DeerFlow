"use client";

import { usePathname } from "next/navigation";
import { ThemeProvider as NextThemesProvider } from "next-themes";

export function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  const pathname = usePathname();
  const forcedTheme =
    pathname === "/" || pathname === "/login" ? "light" : undefined;
  return (
    <NextThemesProvider
      {...props}
      forcedTheme={forcedTheme}
    >
      {children}
    </NextThemesProvider>
  );
}
