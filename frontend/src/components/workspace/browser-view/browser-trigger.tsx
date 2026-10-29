import { MonitorIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/workspace/tooltip";
import { useI18n } from "@/core/i18n/hooks";

import { useMaybeSidecar } from "../sidecar/context";

import { useMaybeBrowserView } from "./context";

export const BrowserTrigger = ({ inMenu = false }: { inMenu?: boolean }) => {
  const { t } = useI18n();
  const browserView = useMaybeBrowserView();
  const sidecar = useMaybeSidecar();

  if (!browserView) {
    return null;
  }
  const browserVisible = browserView.open && !sidecar?.open;
  const label = browserVisible ? t.common.close : t.common.showBrowser;
  const toggle = () => {
    if (browserVisible) {
      browserView.close();
      return;
    }
    sidecar?.close();
    browserView.openPanel();
  };

  if (inMenu) {
    return (
      <DropdownMenuItem onSelect={toggle}>
        <MonitorIcon />
        {label}
      </DropdownMenuItem>
    );
  }

  return (
    <Tooltip content={label}>
      <Button
        aria-label={label}
        className="text-muted-foreground hover:text-foreground"
        data-testid="browser-trigger"
        size="icon"
        type="button"
        variant={browserVisible ? "secondary" : "ghost"}
        onClick={toggle}
      >
        <MonitorIcon />
      </Button>
    </Tooltip>
  );
};
