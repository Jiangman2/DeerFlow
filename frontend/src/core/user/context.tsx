import { createContext, useContext } from "react";

export type WorkspaceUser = {
  name?: string | null;
  email?: string | null;
} | null;

const WorkspaceUserContext = createContext<WorkspaceUser>(null);

export function WorkspaceUserProvider({
  value,
  children,
}: {
  value: WorkspaceUser;
  children: React.ReactNode;
}) {
  return (
    <WorkspaceUserContext.Provider value={value}>
      {children}
    </WorkspaceUserContext.Provider>
  );
}

export function useWorkspaceUser() {
  return useContext(WorkspaceUserContext);
}

