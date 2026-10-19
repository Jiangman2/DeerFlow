"use client";

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { usePptConfirmationEnabled } from "@/core/features/hooks";
import { useI18n } from "@/core/i18n/hooks";
import { pptConfirmationMessages } from "@/core/i18n/ppt-confirmation";

type Mode = "manual" | "auto_after_60s";
type Draft = { text: string; fileCount: number; hasQuotes: boolean };
type Decision = { mode: Mode | null; isCurrent: () => boolean };
type ChoiceContext = {
  pending: Draft | null;
  request: (draft: Draft) => Promise<Decision>;
  finish: (mode: Mode | null) => void;
};
const Context = createContext<ChoiceContext | null>(null);

export function PptSubmissionChoiceProvider({
  children,
  scope,
}: {
  children: ReactNode;
  scope: string;
}) {
  const [pending, setPending] = useState<Draft | null>(null);
  const scopeToken = useMemo(() => ({ scope }), [scope]);
  const scopeRef = useRef(scopeToken);
  scopeRef.current = scopeToken;
  const generation = useRef(0);
  const active = useRef<{
    resolve: (value: Decision) => void;
    scope: typeof scopeToken;
    generation: number;
  } | null>(null);
  const mounted = useRef(true);
  useLayoutEffect(() => {
    mounted.current = true;
    setPending(null);
    return () => {
      mounted.current = false;
      generation.current += 1;
      const old = active.current;
      active.current = null;
      old?.resolve({ mode: null, isCurrent: () => false });
    };
  }, [scope]);
  const request = useCallback(
    (draft: Draft) => {
      if (
        !mounted.current ||
        scopeRef.current !== scopeToken ||
        active.current
      ) {
        return Promise.reject(new Error("ppt_choice_already_pending"));
      }
      const capturedScope = scopeRef.current;
      const capturedGeneration = ++generation.current;
      setPending(draft);
      return new Promise<Decision>((resolve) => {
        active.current = {
          resolve,
          scope: capturedScope,
          generation: capturedGeneration,
        };
      });
    },
    [scopeToken],
  );
  const finish = useCallback(
    (mode: Mode | null) => {
      const old = active.current;
      if (old?.scope !== scopeToken || scopeRef.current !== scopeToken) return;
      active.current = null; // Synchronous single admission, before resolving.
      setPending(null);
      const isCurrent = () =>
        mounted.current &&
        scopeRef.current === old.scope &&
        generation.current === old.generation;
      old.resolve({ mode: isCurrent() ? mode : null, isCurrent });
    },
    [scopeToken],
  );
  const value = useMemo(
    () => ({ pending, request, finish }),
    [pending, request, finish],
  );
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function usePptSubmissionChoice() {
  return useContext(Context);
}

function ChoiceCard({
  draft,
  finish,
}: {
  draft: Draft;
  finish: ChoiceContext["finish"];
}) {
  const { locale } = useI18n();
  const copy = pptConfirmationMessages(locale);
  const automaticAvailable = usePptConfirmationEnabled(true);
  const [mode, setMode] = useState<Mode>("manual");
  const manualRef = useRef<HTMLInputElement>(null);
  useLayoutEffect(() => {
    manualRef.current?.focus({ preventScroll: true });
  }, []);
  return (
    <section
      className="cnooc-ppt-submission-card w-full min-w-0 rounded-2xl border border-blue-100 bg-white p-4 text-left shadow-sm sm:p-5"
      aria-labelledby="ppt-submission-choice-title"
      data-testid="ppt-submission-choice"
    >
      <p className="text-muted-foreground mb-2 text-xs">{copy.beforeStart}</p>
      <h2
        id="ppt-submission-choice-title"
        className="mb-3 text-base font-semibold"
      >
        {copy.chooseMode}
      </h2>
      <p
        className="mb-3 max-h-24 overflow-auto rounded-lg bg-slate-50 p-3 text-sm break-words whitespace-pre-wrap"
        data-testid="ppt-pending-request"
      >
        {draft.text}
      </p>
      {draft.fileCount > 0 && (
        <p className="text-muted-foreground mb-2 text-xs">
          {copy.attachedFiles}: {draft.fileCount}
        </p>
      )}
      <fieldset className="space-y-2">
        <legend className="sr-only">{copy.chooseMode}</legend>
        <label className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm">
          <input
            ref={manualRef}
            type="radio"
            name="ppt-submission-mode"
            value="manual"
            checked={mode === "manual"}
            onChange={() => setMode("manual")}
            className="mt-1"
          />
          <span>
            <span className="block font-medium">{copy.manualOption}</span>
            <span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
              {copy.manualOptionHelp}
            </span>
          </span>
        </label>
        <label className="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm has-disabled:opacity-50">
          <input
            type="radio"
            name="ppt-submission-mode"
            value="auto_after_60s"
            disabled={draft.hasQuotes || !automaticAvailable}
            checked={mode === "auto_after_60s"}
            onChange={() => setMode("auto_after_60s")}
            className="mt-1"
          />
          <span>
            <span className="block font-medium">{copy.autoOption}</span>
            <span className="text-muted-foreground mt-1 block text-xs leading-relaxed">
              {draft.hasQuotes
                ? copy.quoted
                : !automaticAvailable
                  ? copy.autoUnavailable
                  : copy.autoOptionHelp}
            </span>
          </span>
        </label>
      </fieldset>
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button
          type="button"
          className="min-h-11"
          variant="outline"
          onClick={() => finish(null)}
        >
          {copy.editRequest}
        </Button>
        <Button
          type="button"
          className="min-h-11"
          disabled={
            mode === "auto_after_60s" &&
            (!automaticAvailable || draft.hasQuotes)
          }
          onClick={() => finish(mode)}
        >
          {copy.startTask}
        </Button>
      </div>
    </section>
  );
}

export function PptSubmissionChoiceSurface({
  children = null,
}: {
  children?: ReactNode;
}) {
  const choice = usePptSubmissionChoice();
  return choice?.pending ? (
    <ChoiceCard draft={choice.pending} finish={choice.finish} />
  ) : (
    children
  );
}
