"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { z } from "zod";

import { useAuth } from "@/core/auth/AuthProvider";

const messageSchema = z.object({
  id: z.string(),
  role: z.enum(["user", "assistant"]),
  text: z.string(),
  think: z.string(),
});
const recordSchema = z.object({
  id: z.string(),
  title: z.string(),
  updatedAt: z.number(),
  messages: z.array(messageSchema),
  sessionId: z.string().optional(),
  draft: z.string().default(""),
});
export type HainengMessage = z.infer<typeof messageSchema>;
export type HainengRecord = z.infer<typeof recordSchema>;
type History = {
  records: HainengRecord[];
  activeId: string;
  ready: boolean;
  busy: boolean;
  openVersion: number;
  workVersion: number;
  openWork: () => void;
  save: (record: HainengRecord) => void;
  open: (id: string) => void;
  create: () => void;
  setBusy: (value: boolean) => void;
  storageError: string;
};
const Context = createContext<History | null>(null);

/** Browser-local history only. No SESSION credentials are stored here. */
export function HainengHistoryProvider({
  children,
  preview = false,
  namespace = "chat",
}: {
  children: ReactNode;
  preview?: boolean;
  namespace?: string;
}) {
  const { user } = useAuth();
  const scope = `${preview ? "preview" : "live"}:${namespace}:${user?.id ?? "anonymous"}`;
  return (
    <ScopedHistory
      key={scope}
      storageKey={`deerflow:haineng:history:v1:${scope}`}
    >
      {children}
    </ScopedHistory>
  );
}
function ScopedHistory({
  children,
  storageKey,
}: {
  children: ReactNode;
  storageKey: string;
}) {
  const [records, setRecords] = useState<HainengRecord[]>([]);
  const [activeId, setActiveId] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [openVersion, setOpenVersion] = useState(0);
  const [workVersion, setWorkVersion] = useState(0);
  const [storageError, setStorageError] = useState("");
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      const data = z
        .object({ records: z.array(recordSchema), activeId: z.string() })
        .parse(raw ? JSON.parse(raw) : { records: [], activeId: "" });
      setRecords(data.records);
      setActiveId(data.activeId || crypto.randomUUID());
    } catch {
      setStorageError("本地历史记录读取失败；现有数据未被覆盖。");
      setActiveId(crypto.randomUUID());
    }
    setReady(true);
  }, [storageKey]);
  const save = useCallback((record: HainengRecord) => {
    setRecords((old) =>
      [record, ...old.filter((r) => r.id !== record.id)].sort(
        (a, b) => b.updatedAt - a.updatedAt,
      ),
    );
  }, []);
  useEffect(() => {
    if (!ready || storageError) return;
    try {
      localStorage.setItem(storageKey, JSON.stringify({ records, activeId }));
    } catch {
      setStorageError("浏览器存储不可用或已满，当前对话尚未持久保存。");
    }
  }, [records, activeId, ready, storageKey, storageError]);
  return (
    <Context.Provider
      value={{
        records,
        activeId,
        ready,
        busy,
        openVersion,
        workVersion,
        openWork: () => setWorkVersion((v) => v + 1),
        save,
        storageError,
        setBusy,
        open: (id) => {
          if (!busy) {
            setActiveId(id);
            setOpenVersion((v) => v + 1);
          }
        },
        create: () => {
          if (!busy) {
            setActiveId(crypto.randomUUID());
            setOpenVersion((v) => v + 1);
          }
        },
      }}
    >
      {children}
    </Context.Provider>
  );
}
export function useHainengHistory() {
  const value = useContext(Context);
  if (!value) throw new Error("Missing HainengHistoryProvider");
  return value;
}
