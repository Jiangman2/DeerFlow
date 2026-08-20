"use client";

import {
  BellIcon,
  CableIcon,
  InfoIcon,
  BrainIcon,
  MegaphoneIcon,
  PaletteIcon,
  SparklesIcon,
  UserIcon,
  UsersIcon,
  WrenchIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AboutSettingsPage } from "@/components/workspace/settings/about-settings-page";
import { AccountSettingsPage } from "@/components/workspace/settings/account-settings-page";
import { AnnouncementSettingsPage } from "@/components/workspace/settings/announcement-settings-page";
import { AppearanceSettingsPage } from "@/components/workspace/settings/appearance-settings-page";
import { ChannelsSettingsPage } from "@/components/workspace/settings/channels-settings-page";
import { MemorySettingsPage } from "@/components/workspace/settings/memory-settings-page";
import { NotificationSettingsPage } from "@/components/workspace/settings/notification-settings-page";
import { SkillSettingsPage } from "@/components/workspace/settings/skill-settings-page";
import { ToolSettingsPage } from "@/components/workspace/settings/tool-settings-page";
import { UserManagementSettingsPage } from "@/components/workspace/settings/user-management-settings-page";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

type SettingsSection =
  | "account"
  | "announcements"
  | "appearance"
  | "channels"
  | "memory"
  | "tools"
  | "skills"
  | "users"
  | "notification"
  | "about";

type SettingsDialogProps = React.ComponentProps<typeof Dialog> & {
  defaultSection?: SettingsSection;
};

export function SettingsDialog(props: SettingsDialogProps) {
  const { defaultSection = "appearance", ...dialogProps } = props;
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const [activeSection, setActiveSection] =
    useState<SettingsSection>(defaultSection);

  useEffect(() => {
    // When opening the dialog, ensure the active section follows the caller's intent.
    // This allows triggers like "About" to open the dialog directly on that page.
    if (dialogProps.open) {
      setActiveSection(defaultSection);
    }
  }, [defaultSection, dialogProps.open]);

  const sections = useMemo(
    () => [
      {
        id: "account",
        label: t.settings.sections.account,
        icon: UserIcon,
      },
      {
        id: "appearance",
        label: t.settings.sections.appearance,
        icon: PaletteIcon,
      },
      {
        id: "notification",
        label: t.settings.sections.notification,
        icon: BellIcon,
      },
      {
        id: "channels",
        label: t.settings.sections.channels,
        icon: CableIcon,
      },
      {
        id: "memory",
        label: t.settings.sections.memory,
        icon: BrainIcon,
      },
      { id: "tools", label: t.settings.sections.tools, icon: WrenchIcon },
      { id: "skills", label: t.settings.sections.skills, icon: SparklesIcon },
      ...(isAdmin
        ? [
            { id: "users", label: "用户管理", icon: UsersIcon },
            { id: "announcements", label: "公告", icon: MegaphoneIcon },
          ]
        : []),
      { id: "about", label: t.settings.sections.about, icon: InfoIcon },
    ],
    [
      t.settings.sections.account,
      t.settings.sections.appearance,
      t.settings.sections.channels,
      t.settings.sections.memory,
      t.settings.sections.tools,
      t.settings.sections.skills,
      t.settings.sections.notification,
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
        className="mobile-fullscreen-surface app-safe-area flex w-screen max-w-none min-w-0 flex-col gap-3 overflow-hidden rounded-none border-x-0 p-4 sm:h-[calc(100vh-2rem)] sm:max-h-[56rem] sm:w-[calc(100vw-2rem)] sm:max-w-5xl sm:rounded-lg sm:border sm:p-6 md:max-w-6xl"
        aria-describedby={undefined}
        data-testid="settings-dialog"
      >
        <DialogHeader className="shrink-0 gap-1 pr-8 text-left">
          <DialogTitle>{t.settings.title}</DialogTitle>
          <p className="text-muted-foreground text-sm">
            {t.settings.description}
          </p>
        </DialogHeader>
        <div className="grid min-h-0 min-w-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-3 overflow-hidden md:grid-cols-[220px_minmax(0,1fr)] md:grid-rows-1 md:gap-4">
          <nav className="app-scroll-region bg-sidebar min-w-0 overflow-x-auto overflow-y-hidden rounded-lg border p-2 md:min-h-0 md:overflow-x-hidden md:overflow-y-auto">
            <ul className="flex w-max min-w-full gap-1 md:block md:w-auto md:min-w-0 md:space-y-1 md:pr-1">
              {sections.map(({ id, label, icon: Icon }) => {
                const active = activeSection === id;
                return (
                  <li key={id}>
                    <button
                      type="button"
                      onClick={() => setActiveSection(id as SettingsSection)}
                      className={cn(
                        "flex min-h-10 shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors md:w-full md:gap-3",
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
          <ScrollArea className="app-scroll-region h-full min-h-0 min-w-0 rounded-lg border">
            <div className="min-w-0 space-y-8 p-4 sm:p-6">
              {activeSection === "account" && <AccountSettingsPage />}
              {activeSection === "announcements" && isAdmin && (
                <AnnouncementSettingsPage />
              )}
              {activeSection === "appearance" && <AppearanceSettingsPage />}
              {activeSection === "memory" && <MemorySettingsPage />}
              {activeSection === "tools" && <ToolSettingsPage />}
              {activeSection === "skills" && (
                <SkillSettingsPage
                  onClose={() => props.onOpenChange?.(false)}
                />
              )}
              {activeSection === "notification" && <NotificationSettingsPage />}
              {activeSection === "channels" && <ChannelsSettingsPage />}
              {activeSection === "users" && isAdmin && (
                <UserManagementSettingsPage />
              )}
              {activeSection === "about" && <AboutSettingsPage />}
            </div>
          </ScrollArea>
        </div>
      </DialogContent>
    </Dialog>
  );
}
