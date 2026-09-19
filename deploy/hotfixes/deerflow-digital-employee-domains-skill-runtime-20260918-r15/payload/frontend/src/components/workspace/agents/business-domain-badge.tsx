import { Badge } from "@/components/ui/badge";
import type { DigitalEmployeeCategory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function BusinessDomainBadge({
  domainGroup,
  className,
}: {
  domainGroup: DigitalEmployeeCategory["domain_group"];
  className?: string;
}) {
  const { t } = useI18n();
  const isUnified = domainGroup === "unified";

  return (
    <Badge
      data-testid="business-domain-badge"
      variant={isUnified ? "secondary" : "outline"}
      title={isUnified ? t.agents.unifiedDomains : t.agents.distributedDomains}
      className={cn(
        "h-5 min-w-5 rounded-md px-1.5 py-0 text-[10px] leading-none",
        !isUnified && "bg-background",
        className,
      )}
    >
      {isUnified ? "统" : "分"}
    </Badge>
  );
}
