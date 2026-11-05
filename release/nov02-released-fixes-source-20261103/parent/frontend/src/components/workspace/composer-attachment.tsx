"use client";

import { FileText, X } from "lucide-react";

import { usePromptInputAttachments } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useI18n } from "@/core/i18n/hooks";
import { formatUploadSize, type PromptInputFilePart } from "@/core/uploads";

/** Uses the existing attachment store; selection and upload ownership stay unchanged. */
export function ComposerAttachment({
  data,
  disabled = false,
}: {
  data: PromptInputFilePart & { id: string };
  disabled?: boolean;
}) {
  const attachments = usePromptInputAttachments();
  const { t } = useI18n();
  const name = data.filename?.trim() ? data.filename : t.uploads.attachment;
  const extension = data.filename?.split(".").pop()?.toUpperCase();
  const isImage = data.mediaType?.startsWith("image/") && data.url;
  return (
    <div className="cnooc-attachment-card">
      {isImage ? (
        <Dialog>
          <DialogTrigger asChild>
            <Button
              type="button"
              variant="ghost"
              className="size-11 shrink-0 p-1"
              aria-label={`${t.common.preview}: ${name}`}
            >
              {/* Local File object URLs cannot use the Next.js image optimizer. */}
              <img
                src={data.url}
                alt=""
                className="size-9 rounded-md object-cover"
              />
            </Button>
          </DialogTrigger>
          <DialogContent
            className="cnooc-detail-dialog flex max-h-[90dvh] max-w-[min(94vw,48rem)] flex-col overflow-hidden"
            aria-describedby={undefined}
          >
            <DialogTitle className="max-h-24 shrink-0 overflow-y-auto pr-8 text-left text-base leading-relaxed break-all">
              {name}
            </DialogTitle>
            <div className="bg-muted/40 min-h-0 overflow-auto overscroll-contain rounded-lg">
              <img
                src={data.url}
                alt={name}
                className="max-h-[65dvh] w-full object-contain"
              />
            </div>
          </DialogContent>
        </Dialog>
      ) : (
        <span className="cnooc-attachment-icon" aria-hidden="true">
          <FileText className="size-5" />
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium" title={name}>
          {name}
        </div>
        <div className="mt-0.5 text-xs text-slate-500">
          {extension && extension.length <= 10
            ? extension
            : t.uploads.attachment}
          {data.file ? ` · ${formatUploadSize(data.file.size)}` : ""}
        </div>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="size-11 shrink-0 text-slate-500 hover:text-red-600"
        aria-label={`${t.uploads.removeAttachment}: ${name}`}
        disabled={disabled}
        onClick={() => attachments.remove(data.id)}
      >
        <X className="size-4" />
      </Button>
    </div>
  );
}
