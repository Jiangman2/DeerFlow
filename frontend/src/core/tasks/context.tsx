import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { computeNextSubtask, subtaskNotification } from "./subtask-update";
import { mergePptUsageObservation, type PptUsageCarrier } from "./ppt-usage-observation";
import type { Subtask } from "./types";

export interface SubtaskContextValue {
  tasks: Record<string, Subtask>;
  // Always mirrors the latest `tasks` (updated during render). `updateSubtask`
  // reads/writes through this instead of a closure snapshot so async callers
  // (e.g. a late-resolving backfill) merge into current state, not stale state.
  tasksRef: React.RefObject<Record<string, Subtask>>;
  setTasks: (tasks: Record<string, Subtask>) => void;
}

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasks: {},
  tasksRef: { current: {} },
  setTasks: () => {
    /* noop */
  },
});

function ScopedSubtasksProvider({
  children,
  scopeKey,
}: {
  children: React.ReactNode;
  scopeKey: string;
}) {
  const [tasks, setTasks] = useState<Record<string, Subtask>>({});
  const tasksRef = useRef(tasks);
  const previousScopeKeyRef = useRef(scopeKey);
  // Keep the ref pointing at the freshest state on every render so reads in
  // async callbacks (backfill `.then`) never see a stale map.
  tasksRef.current = tasks;

  // Preserve the mounted chat subtree during route transitions, but clear
  // task cards before paint so an active PPT workflow can never appear in the
  // newly selected conversation. Remounting this provider also remounted the
  // input/stream surface and caused a visible flash after creating a thread.
  useLayoutEffect(() => {
    if (previousScopeKeyRef.current === scopeKey) {
      return;
    }
    // Creating a thread starts on /new with a provisional client id and then
    // adopts the persisted id returned by the first stream. That is one
    // conversation, not a thread switch; preserve its live task timeline
    // across that identity handoff. A real existing->new or existing->existing
    // transition still clears the old conversation before paint.
    if (
      previousScopeKeyRef.current === "new" &&
      scopeKey !== "new"
    ) {
      previousScopeKeyRef.current = scopeKey;
      return;
    }
    previousScopeKeyRef.current = scopeKey;
    tasksRef.current = {};
    setTasks({});
  }, [scopeKey]);

  return (
    <SubtaskContext.Provider value={{ tasks, tasksRef, setTasks }}>
      {children}
    </SubtaskContext.Provider>
  );
}

export function SubtasksProvider({
  children,
  scopeKey = "default",
}: {
  children: React.ReactNode;
  scopeKey?: string;
}) {
  // Next.js deliberately preserves dynamic-segment layouts while navigating
  // between chat threads. Task events are thread-scoped, so pass the segment
  // into the mounted provider; it clears its state in a layout effect before
  // the new conversation is painted.
  return (
    <ScopedSubtasksProvider scopeKey={scopeKey}>
      {children}
    </ScopedSubtasksProvider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string) {
  const { tasks } = useSubtaskContext();
  return tasks[id];
}

export function useUpdateSubtask() {
  const { tasksRef, setTasks } = useSubtaskContext();
  const shouldNotifyAfterRenderRef = useRef(false);
  // No deps: must run after every render to check the ref set during render.
  useEffect(() => {
    if (!shouldNotifyAfterRenderRef.current) {
      return;
    }
    shouldNotifyAfterRenderRef.current = false;
    setTasks({ ...tasksRef.current });
  });

  const updateSubtask = useCallback(
    (
      task: Partial<Subtask> & PptUsageCarrier & { id: string },
      options?: { eager?: boolean },
    ) => {
      // Read the *latest* state via the ref, never a `tasks` snapshot captured in
      // this callback's closure. Without this, an in-flight
      // fetchSubtaskSteps().then(updateSubtask) resolving late would write a stale
      // map, clobbering SSE steps/status and sibling subtasks added meanwhile (#3779).
      const current = tasksRef.current;
      const { next, becameTerminal, changed } = computeNextSubtask(
        current[task.id],
        task,
      );

      const observationTransition = mergePptUsageObservation(current[task.id], next, task);
      const observationChanged = observationTransition.changed;
      current[task.id] = observationTransition.next;

      // Gate on an actual state change, not mere field presence. The terminal
      // ToolMessage is re-parsed on every MessageList render and always carries
      // modelName/usage, so a presence check would setTasks({...}) with a fresh
      // reference each render — an infinite loop. `subtaskNotification` routes a
      // terminal transition through the deferred (after-render) path and skips
      // no-op re-parses entirely.
      const notify = observationChanged
        ? (becameTerminal || (task.status && task.status !== "in_progress") ? "deferred" : "eager")
        : subtaskNotification(task, { becameTerminal, changed });
      if (options?.eager && (changed || observationChanged)) {
        setTasks({ ...current });
      } else if (notify === "eager") {
        setTasks({ ...current });
      } else if (notify === "deferred") {
        shouldNotifyAfterRenderRef.current = true;
      }
    },
    [tasksRef, setTasks],
  );

  return updateSubtask;
}
