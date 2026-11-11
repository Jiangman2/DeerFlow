"use client";

import { useEffect, useState, type ReactNode } from "react";

import { TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  dashboardNavigationCopy,
  dashboardSections,
  isDashboardSection,
  type DashboardSection,
} from "@/core/i18n/dashboard-navigation";

export function useDashboardSection() {
  const [section, setSection] = useState<DashboardSection>("overview");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const sync = () => {
      const value = window.location.hash.slice(1);
      setSection(isDashboardSection(value) ? value : "overview");
      setReady(true);
    };
    sync();
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);
  const select = (value: string) => {
    if (!isDashboardSection(value) || value === section) return;
    setSection(value);
    const url = new URL(window.location.href);
    url.hash = value;
    window.history.pushState(window.history.state, "", url);
  };
  return { section, select, ready };
}

export function DashboardNavigation({ locale }: { locale: string }) {
  const copy = dashboardNavigationCopy(locale);
  return (
    <div className="bg-background sticky top-0 z-10 rounded-lg border p-1 shadow-sm">
      <TabsList
        aria-label={copy.navigation}
        className="grid w-full grid-cols-3 gap-1 group-data-[orientation=horizontal]/tabs:h-auto sm:grid-cols-6"
      >
        {dashboardSections.map((section) => (
          <TabsTrigger
            key={section}
            value={section}
            className="min-h-10 min-w-0 px-2 py-2 text-center leading-tight whitespace-normal"
          >
            {copy.labels[section]}
          </TabsTrigger>
        ))}
      </TabsList>
    </div>
  );
}

export function DashboardSectionContent({
  value,
  children,
}: {
  value: DashboardSection;
  children: ReactNode;
}) {
  // Preserve filters, pagination, and expanded rows when switching sections.
  // Each panel receives an enabled flag so hidden panels do not poll.
  return (
    <TabsContent
      forceMount
      value={value}
      className="flex min-w-0 flex-col gap-6 data-[state=inactive]:hidden"
    >
      {children}
    </TabsContent>
  );
}
