import { FilesIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

import { useMaybeSidecar } from "../sidecar/context";

import { useArtifacts } from "./context";

export const ArtifactTrigger = ({ inMenu = false }: { inMenu?: boolean }) => {
  const { t } = useI18n();
  const { artifacts, openArtifactPanel } = useArtifacts();
  const sidecar = useMaybeSidecar();

  if (!artifacts || artifacts.length === 0) {
    return null;
  }
  const open = () => {
    sidecar?.close();
    openArtifactPanel();
  };
  if (inMenu) {
    return (
      <DropdownMenuItem data-testid="artifact-trigger" onSelect={open}>
        <FilesIcon />
        {t.common.showArtifacts}
      </DropdownMenuItem>
    );
  }
  return (
    <Tooltip content={t.common.showArtifacts}>
      <Button
        aria-label={t.common.showArtifacts}
        className="text-muted-foreground hover:text-foreground size-8 px-0 sm:h-9 sm:w-auto sm:px-4"
        variant="ghost"
        data-testid="artifact-trigger"
        onClick={open}
      >
        <FilesIcon />
        <span className="hidden sm:inline">{t.common.artifacts}</span>
      </Button>
    </Tooltip>
  );
};
