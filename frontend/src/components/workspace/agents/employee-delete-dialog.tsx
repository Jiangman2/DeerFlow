"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { AgentDraftError, getAgent } from "@/core/agents/api";
import { useDeleteAgent } from "@/core/agents/hooks";
import type { Agent } from "@/core/agents/types";
import { useI18n } from "@/core/i18n/hooks";

/** Mount per confirmation: a list refresh must never replace the reviewed version. */
export function EmployeeDeleteDialog({ name, onClose }: { name: string; onClose: () => void }) {
  const { t } = useI18n();
  const [snapshot, setSnapshot] = useState<Agent | null>(null);
  const [failed, setFailed] = useState(false);
  const mutation = useDeleteAgent();
  useEffect(() => {
    let current = true;
    void getAgent(name, "personal").then((value) => {
      if (!current) return;
      if (value.source !== "personal" || !value.draft_revision) setFailed(true);
      else setSnapshot(value);
    }).catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [name]);

  async function confirmDelete() {
    if (!snapshot?.draft_revision || mutation.isPending || mutation.isError) return;
    try {
      await mutation.mutateAsync({ name, expectedDraftRevision: snapshot.draft_revision });
      toast.success(t.agents.deleteSuccess);
      onClose();
    } catch {
      // Keep the reviewed snapshot and require a new confirmation, including
      // after an uncertain network result. Never automatically repeat DELETE.
    }
  }

  const conflict = mutation.error instanceof AgentDraftError && [409, 428].includes(mutation.error.status);
  return <Dialog open onOpenChange={(open) => { if (!open && !mutation.isPending) onClose(); }}>
    <DialogContent role="alertdialog">
      <DialogHeader>
        <DialogTitle>{t.agents.delete}</DialogTitle>
        <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
      </DialogHeader>
      {!snapshot && !failed && <p role="status">{t.common.loading}</p>}
      {snapshot && <div className="flex flex-col gap-2">
        <p className="font-medium break-words">{snapshot.display_name ?? snapshot.name}</p>
        <p className="text-muted-foreground text-sm break-words">{snapshot.name}</p>
        <p className="max-h-48 overflow-y-auto break-words">{snapshot.description}</p>
      </div>}
      {failed && <Alert variant="destructive"><AlertDescription>{t.agents.draftLoadFailed}</AlertDescription></Alert>}
      {mutation.isError && <Alert variant="destructive"><AlertDescription>{conflict ? t.agents.deleteConflict : t.agents.deleteUnconfirmed}</AlertDescription></Alert>}
      <DialogFooter>
        <Button autoFocus variant="outline" disabled={mutation.isPending} onClick={onClose}>{t.common.cancel}</Button>
        <Button variant="destructive" disabled={!snapshot || failed || mutation.isPending || mutation.isError} onClick={() => void confirmDelete()}>
          {mutation.isPending ? t.common.loading : t.common.delete}
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
