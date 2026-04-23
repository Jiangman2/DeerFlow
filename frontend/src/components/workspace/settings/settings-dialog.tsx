"use client";

import {
  BellIcon,
  InfoIcon,
  BrainIcon,
  SparklesIcon,
  WrenchIcon,
  UsersIcon,
} from "lucide-react";
import type { ComponentProps } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { MemorySettingsPage } from "@/components/workspace/settings/memory-settings-page";
import { NotificationSettingsPage } from "@/components/workspace/settings/notification-settings-page";
import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { ToolSettingsPage } from "@/components/workspace/settings/tool-settings-page";
import { UserManagementSettingsPage } from "@/components/workspace/settings/user-management-settings-page";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type SettingsSection =
  | "appearance"
  | "memory"
  | "tools"
  | "skills"
  | "notification"
  | "users"
  | "about";

type SettingsDialogProps = ComponentProps<typeof Dialog> & {
  defaultSection?: SettingsSection;
};

export function SettingsDialog(props: SettingsDialogProps) {
  const { defaultSection = "notification", ...dialogProps } = props;
  const normalizedDefaultSection =
    defaultSection === "appearance" ? "notification" : defaultSection;
  const { t } = useI18n();
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(normalizedDefaultSection);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    // When opening the dialog, ensure the active section follows the caller's intent.
    // This allows triggers like "About" to open the dialog directly on that page.
    if (dialogProps.open) {
      setActiveSection(normalizedDefaultSection);
    }
  }, [dialogProps.open, normalizedDefaultSection]);

  useEffect(() => {
    let cancelled = false;
    if (!dialogProps.open) return;
    void (async () => {
      try {
        const res = await fetch("/api/admin/users/me", { cache: "no-store" });
        if (!res.ok) return;
        const data = (await res.json()) as { isAdmin?: boolean };
        if (!cancelled) setIsAdmin(data.isAdmin === true);
      } catch {
        if (!cancelled) setIsAdmin(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [dialogProps.open]);

  const sections = useMemo(
    () => {
      const base = [
        {
          id: "notification",
          label: t.settings.sections.notification,
          icon: BellIcon,
        },
        {
          id: "memory",
          label: t.settings.sections.memory,
          icon: BrainIcon,
        },
        { id: "tools", label: t.settings.sections.tools, icon: WrenchIcon },
        { id: "skills", label: t.settings.sections.skills, icon: SparklesIcon },
        { id: "about", label: t.settings.sections.about, icon: InfoIcon },
      ] as const;

      if (!isAdmin) return [...base];
      return [
        ...base.slice(0, 4),
        { id: "users", label: t.settings.sections.users, icon: UsersIcon },
        ...base.slice(4),
      ];
    },
    [
      t.settings.sections.memory,
      t.settings.sections.tools,
      t.settings.sections.skills,
      t.settings.sections.notification,
      t.settings.sections.users,
      t.settings.sections.about,
      isAdmin,
    ],
  );
  return (
    <Dialog
      {...dialogProps}
      onOpenChange={(open) => props.onOpenChange?.(open)}
    >
      <DialogContent
        className="flex h-[75vh] max-h-[calc(100vh-2rem)] flex-col sm:max-w-5xl md:max-w-6xl"
        aria-describedby={undefined}
      >
        <DialogHeader className="gap-1">
          <DialogTitle>{t.settings.title}</DialogTitle>
          <p className="text-muted-foreground text-sm">
            {t.settings.description}
          </p>
        </DialogHeader>
        <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[220px_1fr]">
          <nav className="bg-sidebar min-h-0 overflow-y-auto rounded-lg border p-2">
            <ul className="space-y-1 pr-1">
              {sections.map(({ id, label, icon: Icon }) => {
                const active = activeSection === id;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setActiveSection(id as SettingsSection)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                        active
                          ? "bg-primary text-primary-foreground shadow-sm"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <Icon className="size-4" />
                      <span>{label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>
          <ScrollArea className="h-full min-h-0 rounded-lg border">
            <div className="space-y-8 p-6">
              {activeSection === "memory" && <MemorySettingsPage />}
              {activeSection === "tools" && <ToolSettingsPage />}
              {activeSection === "skills" && (
                <SkillSettingsPage
                  onClose={() => props.onOpenChange?.(false)}
                />
              )}
              {activeSection === "notification" && <NotificationSettingsPage />}
              {activeSection === "users" && <UserManagementSettingsPage />}
              {activeSection === "about" && <AboutSettingsPage />}
            </div>
          </ScrollArea>
        </div>
      </DialogContent>
    </Dialog>
  );
}
