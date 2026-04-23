import "@/styles/globals.css";
import "katex/dist/katex.min.css";

import { type Metadata } from "next";

import { brand } from "@/brand";
import { ThemeProvider } from "@/components/theme-provider";
import { I18nProvider } from "@/core/i18n/context";
import { detectLocaleServer } from "@/core/i18n/server";

export const metadata: Metadata = {
  title: brand.productName,
  description: `${brand.organizationName} · ${brand.productName}`,
  icons: {
    icon: "/images/cnooc.png",
    shortcut: "/images/cnooc.png",
    apple: "/images/cnooc.png",
  },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const locale = await detectLocaleServer();
  return (
    <html lang={locale} suppressContentEditableWarning suppressHydrationWarning>
      <body suppressHydrationWarning>
        <ThemeProvider attribute="class" enableSystem disableTransitionOnChange>
          <I18nProvider initialLocale={locale}>{children}</I18nProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
