"use client";

import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeftIcon,
  CalendarClockIcon,
  ClockIcon,
  CopyIcon,
  HelpCircleIcon,
  HistoryIcon,
  ListFilterIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  TriangleAlertIcon,
  Trash2Icon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import {
  ScheduledTaskScheduleInput,
  type ScheduleValue,
} from "@/components/workspace/scheduled-task-schedule-input";
import {
  WorkspaceBody,
  WorkspaceContainer,
} from "@/components/workspace/workspace-container";
import { listAgents } from "@/core/agents/api";
import { useAgentsApiEnabled } from "@/core/agents/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { hasScheduleSpec } from "@/core/scheduled-tasks/cron";
import {
  useCreateScheduledTask,
  useUpdateScheduledTask,
  useDeleteScheduledTask,
  usePauseScheduledTask,
  useResumeScheduledTask,
  useScheduledTasks,
  useTriggerScheduledTask,
  useThreadScheduledTasks,
} from "@/core/scheduled-tasks/hooks";
import {
  DEFAULT_SCHEDULED_MODEL_NAME,
  resolveScheduledModelName,
} from "@/core/scheduled-tasks/model-selection";
import { RECIPES, type Recipe } from "@/core/scheduled-tasks/recipes";
import { useScheduledTaskRunHistory } from "@/core/scheduled-tasks/run-history";
import type {
  ScheduledTask,
  ScheduledTaskRun,
} from "@/core/scheduled-tasks/types";
import { getLocalSettings } from "@/core/settings/local";
import { cn } from "@/lib/utils";

const NONE = "—";
const DEFAULT_ASSISTANT_ID = "lead_agent";

function agentDisplayName(
  assistantId: string | null | undefined,
  leadLabel: string,
): string {
  if (!assistantId || assistantId === DEFAULT_ASSISTANT_ID) {
    return leadLabel;
  }
  return assistantId;
}

function ReuseThreadNotice({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <Alert className="border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
      <TriangleAlertIcon className="text-amber-600 dark:text-amber-400" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{description}</AlertDescription>
    </Alert>
  );
}

function formatTimestamp(value: string | null, locale: string): string {
  if (!value) {
    return NONE;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  // Use a locale-aware short format like "2026-07-03 09:00". Future timestamps
  // (next_run_at) render as an absolute time, not a relative "ago" string.
  const intlLocale = locale === "zh-CN" ? "zh-CN" : "en-US";
  return new Intl.DateTimeFormat(intlLocale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export default function ScheduledTasksPage() {
  const { t, locale } = useI18n();
  const st = t.scheduledTasks;
  const searchParams = useSearchParams();
  const threadId = searchParams.get("thread_id");
  const allTasksQuery = useScheduledTasks();
  const threadTasksQuery = useThreadScheduledTasks(threadId);
  const { models } = useModels();
  const { enabled: agentsApiEnabled, isLoading: agentsApiLoading } =
    useAgentsApiEnabled();
  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: listAgents,
    enabled: !agentsApiLoading && agentsApiEnabled,
    retry: false,
  });
  const data = threadId ? threadTasksQuery.data : allTasksQuery.data;
  const queryError = threadId ? threadTasksQuery.error : allTasksQuery.error;
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [contextMode, setContextMode] = useState<
    "fresh_thread_per_run" | "reuse_thread"
  >(threadId ? "reuse_thread" : "fresh_thread_per_run");
  const [targetThreadId, setTargetThreadId] = useState(threadId ?? "");
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [createAssistantId, setCreateAssistantId] =
    useState(DEFAULT_ASSISTANT_ID);
  const [createModelName, setCreateModelName] = useState(
    DEFAULT_SCHEDULED_MODEL_NAME,
  );
  const [createSchedule, setCreateSchedule] = useState<ScheduleValue>({
    schedule_type: "cron",
    schedule_spec: { cron: "0 9 * * *" },
    timezone: "",
  });
  const [statusFilter, setStatusFilter] = useState<
    "all" | "enabled" | "paused" | "running" | "completed" | "failed"
  >("all");
  const [typeFilter, setTypeFilter] = useState<
    "all" | "once" | "cron" | "interval"
  >("all");
  const [formError, setFormError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [adviceOpen, setAdviceOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTaskId, setEditTaskId] = useState<string | undefined>(undefined);
  const [editTitle, setEditTitle] = useState("");
  const [editPrompt, setEditPrompt] = useState("");
  const [editAssistantId, setEditAssistantId] = useState(DEFAULT_ASSISTANT_ID);
  const [editModelName, setEditModelName] = useState(
    DEFAULT_SCHEDULED_MODEL_NAME,
  );
  const [editSchedule, setEditSchedule] = useState<ScheduleValue>({
    schedule_type: "cron",
    schedule_spec: { cron: "0 9 * * *" },
    timezone: "UTC",
  });
  const [createNonce, setCreateNonce] = useState(0);
  const agentOptions = useMemo(() => {
    const names = new Set((agentsQuery.data ?? []).map((agent) => agent.name));
    const options = [
      { value: DEFAULT_ASSISTANT_ID, label: st.create.leadAgent },
      ...(agentsQuery.data ?? [])
        .filter((agent) => agent.name !== DEFAULT_ASSISTANT_ID)
        .map((agent) => ({ value: agent.name, label: agent.name })),
    ];
    for (const extra of [createAssistantId, editAssistantId]) {
      if (extra && extra !== DEFAULT_ASSISTANT_ID && !names.has(extra)) {
        options.push({ value: extra, label: extra });
        names.add(extra);
      }
    }
    return options;
  }, [
    agentsQuery.data,
    createAssistantId,
    editAssistantId,
    st.create.leadAgent,
  ]);
  const filteredData = (data ?? []).filter((task) => {
    const statusPass = statusFilter === "all" || task.status === statusFilter;
    const typePass = typeFilter === "all" || task.schedule_type === typeFilter;
    return statusPass && typePass;
  });
  const selectedTask =
    filteredData.find((task) => task.id === selectedTaskId) ?? filteredData[0];
  const selectedTaskModelName =
    typeof selectedTask?.run_context?.model_name === "string"
      ? selectedTask.run_context.model_name
      : undefined;
  const taskRunsQuery = useScheduledTaskRunHistory(selectedTask?.id);
  const createTask = useCreateScheduledTask();
  const updateTask = useUpdateScheduledTask(selectedTask?.id ?? "");
  const pauseTask = usePauseScheduledTask();
  const resumeTask = useResumeScheduledTask();
  const triggerTask = useTriggerScheduledTask();
  const deleteTask = useDeleteScheduledTask();

  const buildRunContext = (
    modelName: string,
    existingContext?: Record<string, unknown>,
  ) => {
    const localContext = getLocalSettings().context;
    const effectiveContext = {
      ...localContext,
      ...existingContext,
    };
    const resolvedModelName = resolveScheduledModelName(models, modelName);
    const mode =
      effectiveContext.mode === "flash" ||
      effectiveContext.mode === "thinking" ||
      effectiveContext.mode === "pro" ||
      effectiveContext.mode === "ultra"
        ? effectiveContext.mode
        : "ultra";
    return {
      ...effectiveContext,
      model_name: resolvedModelName,
      mode,
      thinking_enabled: mode !== "flash",
      is_plan_mode: mode === "pro" || mode === "ultra",
      subagent_enabled: mode === "ultra",
      reasoning_effort:
        effectiveContext.reasoning_effort ??
        (mode === "ultra"
          ? "high"
          : mode === "pro"
            ? "medium"
            : mode === "thinking"
              ? "low"
              : undefined),
    };
  };

  const scheduleTypeLabel = (v: string) =>
    v === "cron"
      ? st.scheduleType.cron
      : v === "once"
        ? st.scheduleType.once
        : v === "interval"
          ? st.scheduleType.interval
          : v;
  const statusLabel = (v: string) =>
    (st.status as Record<string, string>)[v] ?? v;
  const statusTone = (v: string) =>
    ({
      enabled: "border-emerald-200 bg-emerald-50 text-emerald-700",
      running: "border-blue-200 bg-blue-50 text-blue-700",
      paused: "border-amber-200 bg-amber-50 text-amber-700",
      completed: "border-slate-200 bg-slate-50 text-slate-600",
      failed: "border-red-200 bg-red-50 text-red-700",
    })[v] ?? "border-slate-200 bg-slate-50 text-slate-600";
  const contextModeLabel = (v: string) =>
    v === "fresh_thread_per_run"
      ? st.context.fresh
      : v === "reuse_thread"
        ? st.context.reuse
        : v;
  const runTriggerLabel = (v: string) =>
    (st.runTrigger as Record<string, string>)[v] ?? v;
  const runStatusLabel = (v: string) =>
    (st.runStatus as Record<string, string>)[v] ?? v;
  const taskSummary = (task: ScheduledTask) =>
    `${scheduleTypeLabel(task.schedule_type)} · ${statusLabel(task.status)}`;
  const runSummary = (run: ScheduledTaskRun) =>
    `${runTriggerLabel(run.trigger)} · ${runStatusLabel(run.status)}`;
  const scheduleExpression = (task: ScheduledTask) => {
    const spec = task.schedule_spec as {
      cron?: unknown;
      run_at?: unknown;
      every_seconds?: unknown;
    };
    if (task.schedule_type === "cron") {
      return `cron: ${typeof spec.cron === "string" ? spec.cron : NONE}`;
    }
    if (task.schedule_type === "interval") {
      return `every_seconds: ${
        typeof spec.every_seconds === "number" ? spec.every_seconds : NONE
      }`;
    }
    return `run_at: ${
      typeof spec.run_at === "string"
        ? formatTimestamp(spec.run_at, locale)
        : NONE
    }`;
  };
  const applyRecipe = (recipe: Recipe) => {
    const labels = st.recipes[recipe.titleKey];
    setTitle(labels.title);
    setPrompt(recipe.prompt);
    setCreateSchedule(recipe.schedule);
    setContextMode("fresh_thread_per_run");
    setCreateNonce((n) => n + 1);
  };
  const duplicateTask = (task: ScheduledTask) => {
    setTitle(`${task.title}${st.actions.duplicateTitleSuffix}`);
    setPrompt(task.prompt);
    setContextMode(task.context_mode);
    setTargetThreadId(task.thread_id ?? "");
    setCreateAssistantId(task.assistant_id ?? DEFAULT_ASSISTANT_ID);
    setCreateModelName(
      resolveScheduledModelName(
        models,
        typeof task.run_context?.model_name === "string"
          ? task.run_context.model_name
          : undefined,
      ),
    );
    setCreateSchedule({
      schedule_type: task.schedule_type,
      schedule_spec: { ...task.schedule_spec },
      timezone: task.timezone,
    });
    setFormError(null);
    setCreateNonce((nonce) => nonce + 1);
    setDetailOpen(false);
    setCreateOpen(true);
  };
  const openTaskDetail = (task: ScheduledTask, startEditing = false) => {
    setSelectedTaskId(task.id);
    setEditing(startEditing);
    setDetailOpen(true);
  };

  useEffect(() => {
    document.title = `${t.sidebar.scheduledTasks} - ${t.pages.appName}`;
  }, [t.pages.appName, t.sidebar.scheduledTasks]);

  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    setCreateModelName((current) => resolveScheduledModelName(models, current));
  }, [models]);

  useEffect(() => {
    if (!selectedTaskId) {
      return;
    }
    const stillVisible = filteredData.some(
      (task) => task.id === selectedTaskId,
    );
    if (!stillVisible) {
      setSelectedTaskId(filteredData[0]?.id ?? null);
      setEditing(false);
    }
  }, [filteredData, selectedTaskId]);

  // Reset before children commit so the keyed input captures this task.
  // Same-id refetches retain the in-progress draft.
  if (editTaskId !== selectedTask?.id) {
    setEditTaskId(selectedTask?.id);
    if (!selectedTask) {
      setEditing(false);
    } else {
      setEditTitle(selectedTask.title);
      setEditPrompt(selectedTask.prompt);
      setEditAssistantId(selectedTask.assistant_id ?? DEFAULT_ASSISTANT_ID);
      const spec = selectedTask.schedule_spec as {
        cron?: string;
        run_at?: string;
        every_seconds?: number;
      };
      setEditSchedule({
        schedule_type: selectedTask.schedule_type,
        schedule_spec: {
          cron: typeof spec.cron === "string" ? spec.cron : undefined,
          run_at: typeof spec.run_at === "string" ? spec.run_at : undefined,
          every_seconds:
            typeof spec.every_seconds === "number"
              ? spec.every_seconds
              : undefined,
        },
        timezone: selectedTask.timezone || "UTC",
      });
    }
  }

  useEffect(() => {
    if (!selectedTask?.id || models.length === 0) {
      return;
    }
    setEditModelName(resolveScheduledModelName(models, selectedTaskModelName));
  }, [models, selectedTask?.id, selectedTaskModelName]);

  const isChineseLocale = locale.startsWith("zh");
  const adviceLabels = {
    chooseTitle: isChineseLocale ? "怎么选" : "How to choose",
    examplesTitle: isChineseLocale ? "cron 常用例子" : "Common cron examples",
    fieldTitle: isChineseLocale ? "5 段顺序" : "5-field order",
    wildcardTitle: isChineseLocale ? "符号含义" : "Symbols",
    wildcardText: isChineseLocale
      ? "* = 每一个；*/n = 每隔 n 个单位"
      : "* = every value; */n = every n units",
  };
  const cronFieldNames = st.create.adviceCronFormat
    .split(/[,，、]/)
    .map((field) => field.trim())
    .filter(Boolean);
  const wildcardDescriptions = adviceLabels.wildcardText
    .split(/[；;]/)
    .map((part) =>
      part
        .trim()
        .replace(/^\*\/n\s*=\s*/, "")
        .replace(/^\*\s*=\s*/, ""),
    );
  const splitCronExample = (example: string) => {
    const separatorIndex = example.search(/[：:]/);
    if (separatorIndex < 0) {
      return { expression: example, description: "" };
    }
    return {
      expression: example.slice(0, separatorIndex).trim(),
      description: example.slice(separatorIndex + 1).trim(),
    };
  };

  return (
    <WorkspaceContainer>
      <WorkspaceBody className="min-w-0 overflow-x-hidden overflow-y-auto">
        <div
          className={cn(
            "cnooc-scheduled-page mx-auto flex w-full max-w-7xl min-w-0 flex-col gap-5 p-4 sm:p-6",
            createOpen ? "h-full" : "min-h-full",
          )}
        >
          <header className="cnooc-page-heading flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-start gap-2">
              <SidebarTrigger className="mt-0.5 -ml-2 shrink-0 md:hidden" />
              <div className="min-w-0">
                <h1 className="text-2xl font-semibold">
                  {createOpen ? st.create.title : t.sidebar.scheduledTasks}
                </h1>
                <p className="text-muted-foreground mt-1 text-sm">
                  {createOpen
                    ? st.create.description
                    : st.detail.emptyDescription}
                </p>
              </div>
            </div>
            <Button
              size="sm"
              variant={createOpen ? "outline" : "default"}
              onClick={() => setCreateOpen((value) => !value)}
            >
              {createOpen ? (
                <ArrowLeftIcon className="size-4" />
              ) : (
                <PlusIcon className="size-4" />
              )}
              {createOpen ? t.common.cancel : st.create.title}
            </Button>
          </header>
          {createOpen && (
            <div
              className="flex min-h-0 max-w-full min-w-0 flex-1 flex-col overflow-hidden rounded-md border bg-white/95 shadow-sm"
              data-testid="scheduled-task-create-form"
            >
              <div
                className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto p-3 sm:p-4"
                data-testid="scheduled-task-create-scroll"
              >
                <div
                  className="min-w-0 shrink-0 rounded-md border bg-slate-50/80 p-3"
                  data-testid="schedule-recipes"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="text-sm font-medium">
                      {st.recipes.label}
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => setAdviceOpen(true)}
                    >
                      <HelpCircleIcon className="size-4" />
                      {st.create.adviceTitle}
                    </Button>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {RECIPES.map((recipe) => (
                      <Button
                        key={recipe.id}
                        variant="outline"
                        size="sm"
                        onClick={() => applyRecipe(recipe)}
                      >
                        <span aria-hidden>{recipe.icon}</span>
                        {st.recipes[recipe.titleKey].title}
                      </Button>
                    ))}
                  </div>
                </div>
                <div className="mt-3 grid min-h-0 min-w-0 items-start">
                  <div className="flex min-h-0 min-w-0 flex-col gap-2.5">
                    <div className="flex shrink-0 flex-wrap gap-2">
                      <Button
                        variant={
                          contextMode === "fresh_thread_per_run"
                            ? "default"
                            : "outline"
                        }
                        size="sm"
                        onClick={() => setContextMode("fresh_thread_per_run")}
                      >
                        {st.context.fresh}
                      </Button>
                      <Button
                        variant={
                          contextMode === "reuse_thread" ? "default" : "outline"
                        }
                        size="sm"
                        onClick={() => setContextMode("reuse_thread")}
                      >
                        {st.context.reuse}
                      </Button>
                    </div>
                    {contextMode === "reuse_thread" && (
                      <>
                        <Input
                          value={targetThreadId}
                          onChange={(event) =>
                            setTargetThreadId(event.target.value)
                          }
                          placeholder={st.context.threadIdPlaceholder}
                        />
                        <ReuseThreadNotice
                          title={st.context.reuseNoticeTitle}
                          description={st.context.reuseNoticeDescription}
                        />
                      </>
                    )}
                    <Input
                      className="h-10"
                      value={title}
                      onChange={(event) => setTitle(event.target.value)}
                      placeholder={st.create.taskTitle}
                    />
                    <Textarea
                      className="min-h-24 resize-none"
                      rows={4}
                      value={prompt}
                      onChange={(event) => setPrompt(event.target.value)}
                      placeholder={st.create.prompt}
                    />
                    <div className="grid gap-1.5">
                      <label
                        className="text-sm font-medium"
                        htmlFor="scheduled-task-agent"
                      >
                        {st.create.agent}
                      </label>
                      <Select
                        value={createAssistantId}
                        onValueChange={setCreateAssistantId}
                      >
                        <SelectTrigger
                          id="scheduled-task-agent"
                          className="w-full"
                          data-testid="scheduled-task-create-agent"
                        >
                          <SelectValue placeholder={st.create.agent} />
                        </SelectTrigger>
                        <SelectContent>
                          {agentOptions.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="grid gap-1.5">
                      <label
                        className="text-sm font-medium"
                        htmlFor="scheduled-task-model"
                      >
                        {st.model.label}
                      </label>
                      <Select
                        value={createModelName}
                        onValueChange={setCreateModelName}
                        disabled={models.length === 0}
                      >
                        <SelectTrigger
                          id="scheduled-task-model"
                          className="w-full"
                          data-testid="scheduled-task-model"
                        >
                          <SelectValue placeholder={st.model.placeholder} />
                        </SelectTrigger>
                        <SelectContent>
                          {models.map((model) => (
                            <SelectItem key={model.name} value={model.name}>
                              {model.display_name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <p className="text-muted-foreground text-xs">
                        {st.model.hint}
                      </p>
                    </div>
                    <ScheduledTaskScheduleInput
                      key={createNonce}
                      initial={createSchedule}
                      onChange={setCreateSchedule}
                    />
                    {formError && (
                      <div className="text-destructive text-sm">
                        {formError}
                      </div>
                    )}
                  </div>
                </div>
              </div>
              <div
                className="flex shrink-0 flex-wrap justify-end gap-2 border-t bg-white/95 p-3 backdrop-blur-sm sm:px-4"
                data-testid="scheduled-task-create-actions"
              >
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  {t.common.cancel}
                </Button>
                <Button
                  onClick={() => {
                    const hasSchedule = hasScheduleSpec(
                      createSchedule.schedule_spec,
                    );
                    if (
                      !title ||
                      !prompt ||
                      !createModelName ||
                      !hasSchedule ||
                      (contextMode === "reuse_thread" && !targetThreadId)
                    ) {
                      setFormError(st.create.fillRequired);
                      return;
                    }
                    setFormError(null);
                    createTask.mutate(
                      {
                        context_mode: contextMode,
                        thread_id:
                          contextMode === "reuse_thread"
                            ? targetThreadId
                            : null,
                        assistant_id: createAssistantId,
                        title,
                        prompt,
                        run_context: buildRunContext(createModelName),
                        schedule_type: createSchedule.schedule_type,
                        schedule_spec: createSchedule.schedule_spec,
                        timezone: createSchedule.timezone || "UTC",
                      },
                      {
                        onSuccess: () => {
                          // Clear the form so a follow-up task starts fresh.
                          setTitle("");
                          setPrompt("");
                          setCreateAssistantId(DEFAULT_ASSISTANT_ID);
                          setCreateModelName(resolveScheduledModelName(models));
                          setTargetThreadId("");
                          setContextMode("fresh_thread_per_run");
                          setCreateSchedule({
                            schedule_type: "cron",
                            schedule_spec: { cron: "0 9 * * *" },
                            timezone: "",
                          });
                          setCreateNonce((n) => n + 1);
                          setCreateOpen(false);
                        },
                      },
                    );
                  }}
                  disabled={
                    !title ||
                    !prompt ||
                    !createModelName ||
                    !hasScheduleSpec(createSchedule.schedule_spec) ||
                    (contextMode === "reuse_thread" && !targetThreadId) ||
                    createTask.isPending
                  }
                >
                  {st.create.submit}
                </Button>
              </div>
            </div>
          )}
          {!createOpen && (
            <>
              {threadId && (
                <div className="text-muted-foreground text-sm">
                  {st.detail.filteredByThread.replace("{id}", threadId)}
                </div>
              )}
              {queryError ? (
                <div
                  className="text-destructive text-sm"
                  data-testid="scheduled-task-load-error"
                >
                  {st.detail.loadFailed}: {queryError.message}
                </div>
              ) : null}
              <div className="flex flex-wrap items-center gap-2 rounded-md border bg-white p-3 shadow-sm">
                <div className="text-muted-foreground mr-2 flex items-center gap-2 text-sm font-medium">
                  <ListFilterIcon className="size-4" />
                  {st.filters.label}
                </div>
                <Button
                  variant={statusFilter === "all" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter("all")}
                >
                  {st.filters.allStatuses}
                </Button>
                <Button
                  variant={statusFilter === "enabled" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter("enabled")}
                >
                  {st.filters.enabled}
                </Button>
                <Button
                  variant={statusFilter === "paused" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter("paused")}
                >
                  {st.filters.paused}
                </Button>
                <Button
                  variant={statusFilter === "completed" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter("completed")}
                >
                  {st.filters.completed}
                </Button>
                <Button
                  variant={statusFilter === "failed" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setStatusFilter("failed")}
                >
                  {st.filters.failed}
                </Button>
                <Button
                  variant={typeFilter === "all" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTypeFilter("all")}
                >
                  {st.filters.allTypes}
                </Button>
                <Button
                  variant={typeFilter === "cron" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTypeFilter("cron")}
                >
                  {st.filters.cron}
                </Button>
                <Button
                  variant={typeFilter === "once" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTypeFilter("once")}
                >
                  {st.filters.once}
                </Button>
                <Button
                  variant={typeFilter === "interval" ? "default" : "outline"}
                  size="sm"
                  onClick={() => setTypeFilter("interval")}
                >
                  {st.filters.interval}
                </Button>
              </div>
              {filteredData.length === 0 ? (
                <div
                  data-testid="scheduled-task-list"
                  className="flex min-h-0 flex-1 items-center justify-center rounded-md border border-dashed bg-white/75 p-10 text-center shadow-sm"
                >
                  <div className="max-w-md">
                    <div className="mx-auto flex size-12 items-center justify-center rounded-md bg-blue-50 text-blue-700">
                      <CalendarClockIcon className="size-6" />
                    </div>
                    <div className="mt-4 text-lg font-semibold">
                      {st.detail.emptyTitle}
                    </div>
                    <div className="text-muted-foreground mt-2 text-sm leading-6">
                      {st.detail.emptyDescription}
                    </div>
                    <Button
                      className="mt-5"
                      onClick={() => setCreateOpen(true)}
                    >
                      <PlusIcon className="size-4" />
                      {st.create.title}
                    </Button>
                  </div>
                </div>
              ) : (
                <div
                  data-testid="scheduled-task-list"
                  className="grid min-h-0 flex-1 auto-rows-max gap-4 overflow-y-auto pb-2 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4"
                >
                  {filteredData.map((task) => {
                    return (
                      <div
                        key={task.id}
                        data-testid={`scheduled-task-item-${task.id}`}
                        className="group hover:border-primary/35 relative flex min-h-56 flex-col overflow-hidden rounded-md border bg-gradient-to-br from-white to-slate-50/70 shadow-sm transition-all duration-300 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-blue-900/5"
                      >
                        <div className="pointer-events-none absolute inset-x-0 top-0 h-1.5 bg-gradient-to-r from-blue-500/45 via-transparent to-cyan-400/30" />
                        <button
                          type="button"
                          onClick={() => openTaskDetail(task)}
                          className="flex flex-1 flex-col p-3.5 text-left"
                        >
                          <div className="flex min-w-0 items-start gap-3">
                            <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-700 ring-1 ring-blue-200/70 transition-transform duration-300 group-hover:scale-105">
                              <CalendarClockIcon className="size-5" />
                            </div>
                            <div className="min-w-0 flex-1">
                              <div className="line-clamp-1 text-base leading-6 font-semibold">
                                {task.title}
                              </div>
                              <div className="text-muted-foreground mt-1 text-sm">
                                {taskSummary(task)}
                              </div>
                            </div>
                            <span
                              className={cn(
                                "shrink-0 rounded-full border px-2 py-0.5 text-xs",
                                statusTone(task.status),
                              )}
                            >
                              {statusLabel(task.status)}
                            </span>
                          </div>

                          <div className="text-muted-foreground mt-3 line-clamp-2 text-sm leading-6">
                            {task.prompt}
                          </div>

                          <div className="text-muted-foreground mt-auto grid gap-1.5 pt-3 text-xs">
                            <div className="flex items-center gap-2">
                              <ClockIcon className="size-3.5" />
                              <span className="truncate">
                                {st.detail.nextRun}:{" "}
                                {formatTimestamp(task.next_run_at, locale)}
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <HistoryIcon className="size-3.5" />
                              <span>
                                {task.run_count === 1
                                  ? st.detail.runsCountOne.replace(
                                      "{count}",
                                      String(task.run_count),
                                    )
                                  : st.detail.runsCount.replace(
                                      "{count}",
                                      String(task.run_count),
                                    )}
                              </span>
                            </div>
                          </div>
                        </button>

                        <div className="mt-auto flex items-center justify-between gap-2 border-t bg-white/80 px-3 py-2.5">
                          <Button
                            size="sm"
                            className="flex-1"
                            variant="outline"
                            onClick={() => triggerTask.mutate(task.id)}
                          >
                            <PlayIcon className="size-3.5" />
                            {st.actions.trigger}
                          </Button>
                          <div className="flex gap-1">
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8"
                              title={
                                task.status === "paused"
                                  ? st.actions.resume
                                  : st.actions.pause
                              }
                              onClick={() =>
                                task.status === "paused"
                                  ? resumeTask.mutate(task.id)
                                  : pauseTask.mutate(task.id)
                              }
                              disabled={
                                task.status !== "enabled" &&
                                task.status !== "paused"
                              }
                            >
                              {task.status === "paused" ? (
                                <PlayIcon className="size-3.5" />
                              ) : (
                                <PauseIcon className="size-3.5" />
                              )}
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8"
                              title={st.actions.edit}
                              onClick={() => openTaskDetail(task, true)}
                            >
                              <PencilIcon className="size-3.5" />
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="h-8 w-8"
                              title={st.actions.duplicate}
                              onClick={() => duplicateTask(task)}
                            >
                              <CopyIcon className="size-3.5" />
                            </Button>
                            <Button
                              size="icon"
                              variant="ghost"
                              className="text-destructive hover:text-destructive h-8 w-8 hover:bg-red-500/10"
                              title={st.actions.delete}
                              onClick={() => {
                                setSelectedTaskId(task.id);
                                setDeleteOpen(true);
                              }}
                            >
                              <Trash2Icon className="size-3.5" />
                            </Button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      </WorkspaceBody>

      <Dialog open={adviceOpen} onOpenChange={setAdviceOpen}>
        <DialogContent className="max-h-[86vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <HelpCircleIcon className="size-5 text-blue-600" />
              {st.create.adviceTitle}
            </DialogTitle>
            <DialogDescription>{st.create.description}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <section className="rounded-md border border-blue-100 bg-gradient-to-br from-blue-50/80 via-white to-white p-4">
              <div className="text-sm font-semibold tracking-wide text-blue-700 uppercase">
                {adviceLabels.chooseTitle}
              </div>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <div className="rounded-md bg-white p-3 ring-1 ring-blue-100">
                  <div className="text-foreground font-medium">
                    {st.context.fresh}
                  </div>
                  <p className="text-muted-foreground mt-1 text-sm leading-6">
                    {st.create.adviceFresh}
                  </p>
                </div>
                <div className="rounded-md bg-white p-3 ring-1 ring-blue-100">
                  <div className="text-foreground font-medium">
                    {st.context.reuse}
                  </div>
                  <p className="text-muted-foreground mt-1 text-sm leading-6">
                    {st.create.adviceReuse}
                  </p>
                </div>
              </div>
            </section>
            <section className="rounded-md border p-4">
              <div className="text-sm font-semibold tracking-wide text-blue-700 uppercase">
                {adviceLabels.examplesTitle}
              </div>
              <div className="mt-3 rounded-md bg-slate-50 p-3">
                <p className="text-foreground font-medium">
                  {st.create.adviceCron}
                </p>
                <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_1fr]">
                  <div>
                    <div className="text-xs font-medium text-blue-700">
                      {adviceLabels.fieldTitle}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {cronFieldNames.map((field) => (
                        <span
                          key={field}
                          className="rounded bg-white px-2 py-1 text-xs font-medium text-slate-700 ring-1 ring-slate-200"
                        >
                          {field}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs font-medium text-blue-700">
                      {adviceLabels.wildcardTitle}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <code className="rounded bg-white px-2 py-1 text-xs text-blue-700 ring-1 ring-slate-200">
                        *
                      </code>
                      <span className="text-muted-foreground text-sm">
                        {wildcardDescriptions[0]}
                      </span>
                      <code className="rounded bg-white px-2 py-1 text-xs text-blue-700 ring-1 ring-slate-200">
                        */n
                      </code>
                      <span className="text-muted-foreground text-sm">
                        {wildcardDescriptions[1]}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
              <ul className="mt-3 grid gap-2">
                {st.create.adviceCronExamples.map((example) => {
                  const { expression, description } = splitCronExample(example);
                  return (
                    <li
                      key={example}
                      className="grid gap-2 rounded-md bg-white p-3 ring-1 ring-slate-200 sm:grid-cols-[120px_minmax(0,1fr)]"
                    >
                      <code className="rounded bg-blue-50 px-2 py-1 text-center font-mono text-sm text-blue-700">
                        {expression}
                      </code>
                      <span className="text-muted-foreground text-sm leading-6">
                        {description}
                      </span>
                    </li>
                  );
                })}
              </ul>
              <div className="text-muted-foreground mt-3 rounded-md border border-dashed border-blue-200 bg-blue-50/70 px-3 py-2 text-sm leading-6">
                <a
                  href="https://crontab.guru/"
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-blue-700 hover:underline"
                >
                  {st.cronHelp} ↗
                </a>{" "}
                {st.create.adviceCronExternal}
              </div>
            </section>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent
          className="max-h-[86vh] overflow-y-auto sm:max-w-3xl"
          data-testid="scheduled-task-detail"
        >
          <DialogHeader>
            <DialogTitle>
              {selectedTask?.title ?? st.detail.noSelection}
            </DialogTitle>
            <DialogDescription>
              {selectedTask
                ? `${taskSummary(selectedTask)} · ${st.detail.nextRun}: ${formatTimestamp(selectedTask.next_run_at, locale)}`
                : st.detail.noSelection}
            </DialogDescription>
          </DialogHeader>
          {selectedTask ? (
            <div className="grid gap-4">
              <div className="grid gap-3 rounded-md border bg-slate-50/80 p-4 text-sm">
                <div className="grid gap-2 sm:grid-cols-2">
                  <div className="text-muted-foreground">
                    {st.detail.contextMode}:{" "}
                    <span className="text-foreground">
                      {contextModeLabel(selectedTask.context_mode)}
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    {st.detail.schedule}:{" "}
                    <span className="text-foreground">
                      {scheduleTypeLabel(selectedTask.schedule_type)}
                    </span>
                  </div>
                  <div className="text-muted-foreground break-all">
                    {selectedTask.context_mode === "reuse_thread"
                      ? `${st.detail.thread}: ${selectedTask.thread_id ?? NONE}`
                      : `${st.detail.lastThread}: ${selectedTask.last_thread_id ?? NONE}`}
                  </div>
                  <div className="text-muted-foreground break-all">
                    {st.detail.expression}:{" "}
                    <span className="text-foreground">
                      {scheduleExpression(selectedTask)}
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    {st.model.label}:{" "}
                    <span className="text-foreground">
                      {models.find(
                        (model) =>
                          model.name === selectedTask.run_context?.model_name,
                      )?.display_name ??
                        (typeof selectedTask.run_context?.model_name ===
                        "string"
                          ? selectedTask.run_context.model_name
                          : st.model.unset)}
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    {st.detail.agent}:{" "}
                    <span className="text-foreground">
                      {agentDisplayName(
                        selectedTask.assistant_id,
                        st.create.leadAgent,
                      )}
                    </span>
                  </div>
                  <div className="text-muted-foreground">
                    {st.detail.lastRun}:{" "}
                    <span className="text-foreground">
                      {formatTimestamp(selectedTask.last_run_at, locale)}
                    </span>
                  </div>
                  <div className="text-muted-foreground break-all">
                    {st.detail.lastRunId}:{" "}
                    <span className="text-foreground">
                      {selectedTask.last_run_id ?? NONE}
                    </span>
                  </div>
                </div>
                <div className="text-muted-foreground">
                  {st.detail.lastError}:{" "}
                  <span
                    className={cn(
                      selectedTask.last_error
                        ? "text-destructive"
                        : "text-foreground",
                    )}
                  >
                    {selectedTask.last_error ?? NONE}
                  </span>
                </div>
                {selectedTask.context_mode === "reuse_thread" && (
                  <ReuseThreadNotice
                    title={st.context.reuseNoticeTitle}
                    description={st.context.reuseNoticeDescription}
                  />
                )}
              </div>

              {editing ? (
                <div className="grid gap-3 rounded-md border p-4">
                  <Input
                    value={editTitle}
                    onChange={(event) => setEditTitle(event.target.value)}
                    placeholder={st.edit.titlePlaceholder}
                  />
                  <Textarea
                    rows={5}
                    value={editPrompt}
                    onChange={(event) => setEditPrompt(event.target.value)}
                    placeholder={st.edit.promptPlaceholder}
                  />
                  <div className="grid gap-1.5">
                    <label
                      className="text-sm font-medium"
                      htmlFor="scheduled-task-edit-agent"
                    >
                      {st.create.agent}
                    </label>
                    <Select
                      value={editAssistantId}
                      onValueChange={setEditAssistantId}
                    >
                      <SelectTrigger
                        id="scheduled-task-edit-agent"
                        className="w-full"
                        data-testid="scheduled-task-edit-agent"
                      >
                        <SelectValue placeholder={st.create.agent} />
                      </SelectTrigger>
                      <SelectContent>
                        {agentOptions.map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="grid gap-1.5">
                    <label
                      className="text-sm font-medium"
                      htmlFor="scheduled-task-edit-model"
                    >
                      {st.model.label}
                    </label>
                    <Select
                      value={editModelName}
                      onValueChange={setEditModelName}
                      disabled={models.length === 0}
                    >
                      <SelectTrigger
                        id="scheduled-task-edit-model"
                        className="w-full"
                        data-testid="scheduled-task-edit-model"
                      >
                        <SelectValue placeholder={st.model.placeholder} />
                      </SelectTrigger>
                      <SelectContent>
                        {models.map((model) => (
                          <SelectItem key={model.name} value={model.name}>
                            {model.display_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <ScheduledTaskScheduleInput
                    key={selectedTask.id}
                    initial={editSchedule}
                    onChange={setEditSchedule}
                    scheduleTypeLocked
                  />
                  <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => setEditing(false)}>
                      {st.actions.cancelEdit}
                    </Button>
                    <Button
                      onClick={() =>
                        updateTask.mutate(
                          {
                            title: editTitle,
                            prompt: editPrompt,
                            assistant_id: editAssistantId,
                            run_context: buildRunContext(
                              editModelName,
                              selectedTask.run_context,
                            ),
                            schedule_spec: editSchedule.schedule_spec,
                            timezone: editSchedule.timezone || "UTC",
                          },
                          { onSuccess: () => setEditing(false) },
                        )
                      }
                      disabled={updateTask.isPending}
                    >
                      {st.edit.submit}
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="rounded-md border bg-white p-4 text-sm leading-6">
                  {selectedTask.prompt}
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    selectedTask.status === "paused"
                      ? resumeTask.mutate(selectedTask.id)
                      : pauseTask.mutate(selectedTask.id)
                  }
                  disabled={
                    selectedTask.status !== "enabled" &&
                    selectedTask.status !== "paused"
                  }
                >
                  {selectedTask.status === "paused"
                    ? st.actions.resume
                    : st.actions.pause}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => triggerTask.mutate(selectedTask.id)}
                >
                  {st.actions.trigger}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditing((value) => !value)}
                >
                  {editing ? st.actions.cancelEdit : st.actions.edit}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => duplicateTask(selectedTask)}
                >
                  <CopyIcon className="size-3.5" />
                  {st.actions.duplicate}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                >
                  {st.actions.delete}
                </Button>
              </div>

              <div className="grid gap-2">
                <nav
                  aria-label={st.history.navigation}
                  className="flex flex-wrap items-center gap-2"
                >
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      taskRunsQuery.page === 0 || taskRunsQuery.isFetching
                    }
                    onClick={taskRunsQuery.newer}
                  >
                    {st.history.newer}
                  </Button>
                  <span className="text-muted-foreground text-sm">
                    {st.history.page.replace(
                      "{page}",
                      String(taskRunsQuery.page + 1),
                    )}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      !taskRunsQuery.hasOlder || taskRunsQuery.isFetching
                    }
                    onClick={taskRunsQuery.older}
                  >
                    {st.history.older}
                  </Button>
                  {taskRunsQuery.page > 0 ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={taskRunsQuery.latest}
                    >
                      {st.history.latest}
                    </Button>
                  ) : null}
                </nav>
                {taskRunsQuery.page > 0 ? (
                  <p className="text-muted-foreground text-xs">
                    {st.history.paused}
                  </p>
                ) : null}
                {taskRunsQuery.isPending ? (
                  <p role="status" className="text-muted-foreground text-sm">
                    {st.history.loading}
                  </p>
                ) : null}
                {taskRunsQuery.isError ? (
                  <div role="alert" className="text-destructive text-sm">
                    <p>{st.history.loadFailed}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={taskRunsQuery.isFetching}
                      onClick={() => void taskRunsQuery.refetch()}
                    >
                      {st.history.retry}
                    </Button>
                  </div>
                ) : null}
                <div
                  className="text-sm font-medium"
                  data-testid="scheduled-task-runs"
                >
                  {(taskRunsQuery.data ?? []).length === 1
                    ? st.detail.runsCountOne.replace(
                        "{count}",
                        String((taskRunsQuery.data ?? []).length),
                      )
                    : st.detail.runsCount.replace(
                        "{count}",
                        String((taskRunsQuery.data ?? []).length),
                      )}
                </div>
                <div
                  className="grid gap-2"
                  data-testid="scheduled-task-run-list"
                >
                  {(taskRunsQuery.data ?? []).length > 0 ? (
                    (taskRunsQuery.data ?? []).map((run) => (
                      <div
                        key={run.id}
                        className="rounded-md border bg-slate-50/80 p-3 text-sm"
                      >
                        <div className="font-medium">{runSummary(run)}</div>
                        <div className="text-muted-foreground text-xs">
                          {run.run_id ?? NONE}
                        </div>
                        <div className="text-muted-foreground text-xs">
                          {formatTimestamp(run.scheduled_for, locale)}
                        </div>
                        {run.error && (
                          <div className="text-destructive text-xs">
                            {run.error}
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div className="text-muted-foreground rounded-md border border-dashed p-4 text-sm">
                      {st.detail.noRuns}
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Delete confirm — follows the agent-card confirm pattern. */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{st.actions.delete}</DialogTitle>
            <DialogDescription>{st.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteTask.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (selectedTask) {
                  deleteTask.mutate(selectedTask.id, {
                    onSuccess: () => setDeleteOpen(false),
                  });
                }
              }}
              disabled={deleteTask.isPending}
            >
              {deleteTask.isPending ? t.common.loading : st.actions.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </WorkspaceContainer>
  );
}
