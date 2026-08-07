import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

import { useSidebar } from "@/components/ui/sidebar";
import { findPowerPointForPreviewArtifact } from "@/core/artifacts/utils";
import { env } from "@/env";

export interface ArtifactsContextType {
  artifacts: string[];
  setArtifacts: (artifacts: string[]) => void;

  selectedArtifact: string | null;
  select: (artifact: string) => void;
  deselect: () => void;

  open: boolean;
  setOpen: (open: boolean) => void;
}

const ArtifactsContext = createContext<ArtifactsContextType | undefined>(
  undefined,
);

interface ArtifactsProviderProps {
  children: ReactNode;
}

export function ArtifactsProvider({ children }: ArtifactsProviderProps) {
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  // Keep the conversation as the primary surface. Static demos still select
  // their first artifact so the file button is ready, but the artifact pane
  // must be opened deliberately instead of hiding the execution transcript.
  const [open, setOpen] = useState(false);
  const { setOpen: setSidebarOpen } = useSidebar();

  const select = useCallback(
    (artifact: string) => {
      setSelectedArtifact(
        findPowerPointForPreviewArtifact(artifact, artifacts) ?? artifact,
      );
      if (env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true") {
        setSidebarOpen(false);
      }
    },
    [artifacts, setSidebarOpen],
  );

  const deselect = useCallback(() => {
    setSelectedArtifact(null);
    setOpen(false);
  }, []);

  const value: ArtifactsContextType = {
    artifacts,
    setArtifacts,

    open,
    setOpen,

    selectedArtifact,
    select,
    deselect,
  };

  return (
    <ArtifactsContext.Provider value={value}>
      {children}
    </ArtifactsContext.Provider>
  );
}

export function useArtifacts() {
  const context = useContext(ArtifactsContext);
  if (context === undefined) {
    throw new Error("useArtifacts must be used within an ArtifactsProvider");
  }
  return context;
}
