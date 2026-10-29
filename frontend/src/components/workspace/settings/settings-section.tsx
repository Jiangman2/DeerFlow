import { cn } from "@/lib/utils";

export function SettingsSection({
  className,
  title,
  description,
  children,
}: {
  className?: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  const hasHeader = Boolean(title) || Boolean(description);
  return (
    <section className={cn("cnooc-settings-section", className)}>
      {hasHeader && (
        <header className="space-y-2">
          <h2 className="text-lg font-semibold">{title}</h2>
          {description && (
            <div className="text-muted-foreground text-sm">{description}</div>
          )}
        </header>
      )}
      <div className={hasHeader ? "mt-5" : undefined}>{children}</div>
    </section>
  );
}
