import type { LucideIcon } from "lucide-react";

export function ModulePageTitle({
  icon: Icon,
  title,
}: {
  icon: LucideIcon;
  title: string;
}) {
  return (
    <div className="cnooc-module-title">
      <span className="cnooc-module-title-icon" aria-hidden="true">
        <Icon />
      </span>
      <h1>{title}</h1>
    </div>
  );
}
