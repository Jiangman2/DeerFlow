"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { SettingsDialog } from "./settings-dialog";

type SettingsSection =
  | "appearance"
  | "memory"
  | "tools"
  | "skills"
  | "notification"
  | "about";

type WorkspaceSettingsDialogContextValue = {
  openSettings: (section?: SettingsSection, delayMs?: number) => void;
};

const WorkspaceSettingsDialogContext =
  createContext<WorkspaceSettingsDialogContextValue | null>(null);

export function WorkspaceSettingsDialogProvider({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const [open, setOpen] = useState(false);
  const [defaultSection, setDefaultSection] =
    useState<SettingsSection>("appearance");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const openSettings = useCallback(
    (section: SettingsSection = "appearance", delayMs = 0) => {
      setDefaultSection(section);
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      if (delayMs <= 0) {
        setOpen(true);
        return;
      }
      timerRef.current = setTimeout(() => {
        setOpen(true);
        timerRef.current = null;
      }, delayMs);
    },
    [],
  );

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    },
    [],
  );

  return (
    <WorkspaceSettingsDialogContext.Provider value={{ openSettings }}>
      {children}
      <SettingsDialog
        open={open}
        onOpenChange={setOpen}
        defaultSection={defaultSection}
      />
    </WorkspaceSettingsDialogContext.Provider>
  );
}

export function useWorkspaceSettingsDialog() {
  const context = useContext(WorkspaceSettingsDialogContext);
  if (!context) {
    throw new Error(
      "useWorkspaceSettingsDialog must be used within WorkspaceSettingsDialogProvider.",
    );
  }
  return context;
}
