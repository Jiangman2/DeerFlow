import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import type { WorkNotificationTargets } from "@/core/scheduled-tasks/types";

export function ScheduledTaskWorkNotification({
  id,
  value,
  targets,
  loading,
  failed,
  refresh,
  onChange,
}: {
  id: string;
  value: string | null;
  targets?: WorkNotificationTargets;
  loading: boolean;
  failed: boolean;
  refresh: () => void;
  onChange: (value: string | null) => void;
}) {
  const { t } = useI18n();
  const labels = t.scheduledTasks.workNotification;
  const options = !failed && targets?.enabled ? targets.targets : [];
  const missing =
    value !== null && !options.some((target) => target.connection_id === value);
  if (value === null && !targets?.enabled && !loading && !failed) {
    return null;
  }

  const labelsCount = new Map<string, number>();
  for (const { label } of options) {
    labelsCount.set(label, (labelsCount.get(label) ?? 0) + 1);
  }
  const note = loading
    ? labels.loading
    : failed
      ? labels.loadFailed
      : missing
        ? labels.unavailableTarget
        : options.length === 0
          ? labels.noTargets
          : labels.hint;
  return (
    <div className="grid gap-1.5">
      <label className="text-sm font-medium" htmlFor={id}>
        {labels.label}
      </label>
      <select
        id={id}
        className="border-input bg-background focus-visible:ring-ring h-9 w-full rounded-md border px-3 text-sm focus-visible:ring-2"
        aria-describedby={`${id}-help`}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">{labels.off}</option>
        {missing && (
          <option value={value} disabled>
            {labels.unavailableTarget}
          </option>
        )}
        {options.map((target, index) => (
          <option key={target.connection_id} value={target.connection_id}>
            {(labelsCount.get(target.label) ?? 0) > 1
              ? `${target.label} (${index + 1})`
              : target.label}
          </option>
        ))}
      </select>
      <div className="flex flex-wrap items-center gap-2">
        <p
          id={`${id}-help`}
          className="text-muted-foreground text-xs"
          role="status"
        >
          {note}
        </p>
        {(failed || targets?.enabled) && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={refresh}
          >
            {labels.refresh}
          </Button>
        )}
      </div>
    </div>
  );
}
