import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { findOfficeDocumentForPreviewArtifact } from "@/core/artifacts/utils";

export interface ArtifactsContextType {
  artifacts: string[];
  setArtifacts: (artifacts: string[]) => void;

  selectedArtifact: string | null;
  select: (artifact: string) => void;
  deselect: () => void;

  open: boolean;
  setOpen: (open: boolean) => void;
  /** Open the artifact surface only from an explicit user interaction. */
  openArtifactPanel: () => void;
}

const ArtifactsContext = createContext<ArtifactsContextType | undefined>(
  undefined,
);

interface ArtifactsProviderProps {
  children: ReactNode;
  scopeKey?: string;
}

export function ArtifactsProvider({
  children,
  scopeKey = "default",
}: ArtifactsProviderProps) {
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<string | null>(null);
  // Keep the conversation as the primary surface. Static demos still select
  // their first artifact so the file button is ready, but the artifact pane
  // must be opened deliberately instead of hiding the execution transcript.
  const [open, setOpen] = useState(false);
  const previousScopeKeyRef = useRef(scopeKey);

  // Keep the provider mounted during client navigation so the chat does not
  // visibly flash, but clear the previous thread's file surface before the
  // browser paints the new route. A keyed provider remounts the whole chat
  // subtree and was the source of a noticeable flash after the first submit.
  useLayoutEffect(() => {
    if (previousScopeKeyRef.current === scopeKey) {
      return;
    }
    // The first persisted id after /new belongs to the same conversation. Do
    // not erase the artifact cards while the live stream adopts that id.
    if (previousScopeKeyRef.current === "new" && scopeKey !== "new") {
      previousScopeKeyRef.current = scopeKey;
      return;
    }
    previousScopeKeyRef.current = scopeKey;
    setArtifacts([]);
    setSelectedArtifact(null);
    setOpen(false);
  }, [scopeKey]);

  const select = useCallback(
    (artifact: string) => {
      setSelectedArtifact(
        findOfficeDocumentForPreviewArtifact(artifact, artifacts) ?? artifact,
      );
    },
    [artifacts],
  );

  const deselect = useCallback(() => {
    setSelectedArtifact(null);
    setOpen(false);
  }, []);

  // Keep opening separate from passive artifact hydration. A stream update,
  // present_files receipt, or persisted state read may add files, but it must
  // never make the right drawer appear by itself.
  const openArtifactPanel = useCallback(() => {
    setOpen(true);
  }, []);

  const value: ArtifactsContextType = {
    artifacts,
    setArtifacts,

    open,
    setOpen,
    openArtifactPanel,

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
