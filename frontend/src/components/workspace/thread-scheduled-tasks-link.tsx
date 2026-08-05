import { CalendarClock } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";

export function ThreadScheduledTasksLink({ threadId }: { threadId: string }) {
  const { t } = useI18n();
  return (
    <Button
      variant="outline"
      size="sm"
      className="size-8 px-0 sm:h-8 sm:w-auto sm:px-3"
      asChild
    >
      <Link
        aria-label={t.sidebar.scheduledTasks}
        href={`/workspace/scheduled-tasks?thread_id=${encodeURIComponent(threadId)}`}
      >
        <CalendarClock />
        <span className="hidden sm:inline">{t.sidebar.scheduledTasks}</span>
      </Link>
    </Button>
  );
}
