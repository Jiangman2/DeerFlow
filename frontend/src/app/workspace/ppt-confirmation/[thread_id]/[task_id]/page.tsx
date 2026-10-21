"use client";

import { useParams } from "next/navigation";
import { Suspense } from "react";

import { PptConfirmationState } from "@/components/workspace/ppt-confirmation-state";

function ConfirmationEntry() {
  const { thread_id, task_id } = useParams<{
    thread_id: string;
    task_id: string;
  }>();
  return (
    <main className="mx-auto w-full max-w-3xl p-4 sm:p-6">
      <PptConfirmationState
        key={`${thread_id}:${task_id}`}
        threadId={thread_id}
        taskId={task_id}
        entry
      />
    </main>
  );
}

export default function Page() {
  return (
    <Suspense>
      <ConfirmationEntry />
    </Suspense>
  );
}
