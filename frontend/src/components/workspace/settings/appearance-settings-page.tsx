"use client";

import { MonitorSmartphoneIcon, MoonIcon, SunIcon } from "lucide-react";
import { useTheme } from "next-themes";
import { useMemo, type ComponentType, type SVGProps } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { enUS, isLocale, zhCN, type Locale } from "@/core/i18n";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

const languageOptions: { value: Locale; label: string }[] = [
  { value: "en-US", label: enUS.locale.localName },
  { value: "zh-CN", label: zhCN.locale.localName },
];

export function AppearanceSettingsPage() {
  const { t, locale, changeLocale } = useI18n();
  const { theme, setTheme, systemTheme } = useTheme();
  const currentTheme = (theme ?? "system") as "system" | "light" | "dark";

  const themeOptions = useMemo(
    () => [
      {
        id: "system",
        label: t.settings.appearance.system,
        description: t.settings.appearance.systemDescription,
        icon: MonitorSmartphoneIcon,
      },
      {
        id: "light",
        label: t.settings.appearance.light,
        description: t.settings.appearance.lightDescription,
        icon: SunIcon,
      },
      {
        id: "dark",
        label: t.settings.appearance.dark,
        description: t.settings.appearance.darkDescription,
        icon: MoonIcon,
      },
    ],
    [
      t.settings.appearance.dark,
      t.settings.appearance.darkDescription,
      t.settings.appearance.light,
      t.settings.appearance.lightDescription,
      t.settings.appearance.system,
      t.settings.appearance.systemDescription,
    ],
  );

  return (
    <div className="space-y-8">
      <SettingsSection
        title={t.settings.appearance.themeTitle}
        description={t.settings.appearance.themeDescription}
      >
        <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-3">
          {themeOptions.map((option) => (
            <ThemePreviewCard
              key={option.id}
              icon={option.icon}
              label={option.label}
              description={option.description}
              active={currentTheme === option.id}
              mode={option.id as "system" | "light" | "dark"}
              systemTheme={systemTheme}
              onSelect={(value) => setTheme(value)}
            />
          ))}
        </div>
      </SettingsSection>

      <Separator />

      <SettingsSection
        title={t.settings.appearance.languageTitle}
        description={t.settings.appearance.languageDescription}
      >
        <Select
          value={locale}
          onValueChange={(value) => {
            if (isLocale(value)) {
              changeLocale(value);
            }
          }}
        >
          <SelectTrigger className="w-[220px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {languageOptions.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </SettingsSection>
    </div>
  );
}

function ThemePreviewCard({
  icon: Icon,
  label,
  description,
  active,
  mode,
  systemTheme,
  onSelect,
}: {
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  label: string;
  description: string;
  active: boolean;
  mode: "system" | "light" | "dark";
  systemTheme?: string;
  onSelect: (mode: "system" | "light" | "dark") => void;
}) {
  const previewMode =
    mode === "system" ? (systemTheme === "dark" ? "dark" : "light") : mode;
  return (
    <button
      type="button"
      onClick={() => onSelect(mode)}
      aria-pressed={active}
      className={cn(
        "group flex h-full min-w-0 flex-col gap-3 overflow-hidden rounded-lg border p-4 text-left transition-all",
        active
          ? "border-blue-600 shadow-sm ring-2 ring-blue-200"
          : "hover:border-border hover:shadow-sm",
      )}
    >
      <div className="flex items-start gap-3">
        <div className="bg-muted rounded-md p-2">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0 space-y-1">
          <div className="text-sm leading-none font-semibold">{label}</div>
          <p className="text-muted-foreground text-xs leading-snug">
            {description}
          </p>
        </div>
      </div>
      <div
        className={cn(
          "relative w-full min-w-0 overflow-hidden rounded-md border text-xs transition-colors",
          previewMode === "dark"
            ? "border-neutral-800 bg-neutral-900 text-neutral-200"
            : "border-slate-200 bg-white text-slate-900",
        )}
      >
        <div
          className={cn(
            "flex items-center gap-2 border-b px-3 py-2",
            previewMode === "dark" ? "border-neutral-700" : "border-slate-200",
          )}
        >
          <div
            className={cn(
              "h-2 w-2 rounded-full",
              previewMode === "dark" ? "bg-emerald-400" : "bg-emerald-500",
            )}
          />
          <div
            className={cn(
              "h-2 w-10 rounded-full",
              previewMode === "dark" ? "bg-neutral-500" : "bg-slate-300",
            )}
          />
          <div
            className={cn(
              "h-2 w-6 rounded-full",
              previewMode === "dark" ? "bg-neutral-600" : "bg-slate-200",
            )}
          />
        </div>
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(5.5rem,0.75fr)] gap-3 px-3 py-3">
          <div className="min-w-0 space-y-2">
            <div
              className={cn(
                "h-3 w-3/4 rounded-full",
                previewMode === "dark" ? "bg-neutral-600" : "bg-slate-200",
              )}
            />
            <div
              className={cn(
                "h-3 w-1/2 rounded-full",
                previewMode === "dark" ? "bg-neutral-700" : "bg-slate-100",
              )}
            />
            <div
              className={cn(
                "h-[90px] rounded-md border",
                previewMode === "dark"
                  ? "border-neutral-700 bg-neutral-800"
                  : "border-slate-200 bg-slate-50",
              )}
            />
          </div>
          <div className="min-w-0 space-y-2">
            <div className="flex items-center gap-2">
              <div
                className={cn(
                  "h-8 w-8 rounded-md",
                  previewMode === "dark" ? "bg-neutral-700" : "bg-slate-100",
                )}
              />
              <div className="space-y-2">
                <div
                  className={cn(
                    "h-2 w-14 rounded-full",
                    previewMode === "dark" ? "bg-neutral-600" : "bg-slate-200",
                  )}
                />
                <div
                  className={cn(
                    "h-2 w-10 rounded-full",
                    previewMode === "dark" ? "bg-neutral-700" : "bg-slate-100",
                  )}
                />
              </div>
            </div>
            <div
              className={cn(
                "flex flex-col gap-1 rounded-md border border-dashed p-2",
                previewMode === "dark"
                  ? "border-neutral-600"
                  : "border-slate-300",
              )}
            >
              <div
                className={cn(
                  "h-2 w-3/5 rounded-full",
                  previewMode === "dark" ? "bg-neutral-600" : "bg-slate-200",
                )}
              />
              <div
                className={cn(
                  "h-2 w-2/5 rounded-full",
                  previewMode === "dark" ? "bg-neutral-700" : "bg-slate-100",
                )}
              />
            </div>
          </div>
        </div>
      </div>
    </button>
  );
}
