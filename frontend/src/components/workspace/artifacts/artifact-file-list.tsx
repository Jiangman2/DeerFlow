import { DownloadIcon, EyeIcon, LoaderIcon, PackageIcon } from "lucide-react";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardAction, CardHeader, CardTitle } from "@/components/ui/card";
import {
  filterUserVisibleArtifacts,
  urlOfArtifact,
} from "@/core/artifacts/utils";
import { useAuth } from "@/core/auth/AuthProvider";
import { useI18n } from "@/core/i18n/hooks";
import { installSkill, SkillRequestError } from "@/core/skills/api";
import {
  getFileExtensionDisplayName,
  getFileIcon,
  getFileName,
} from "@/core/utils/files";
import { cn } from "@/lib/utils";

import { useArtifacts } from "./context";

export function ArtifactFileList({
  className,
  files,
  threadId,
}: {
  className?: string;
  files: string[];
  threadId: string;
}) {
  const { t } = useI18n();
  const { user } = useAuth();
  const isAdmin = user?.system_role === "admin";
  const {
    artifacts,
    select: selectArtifact,
    openArtifactPanel,
  } = useArtifacts();
  const [installingFile, setInstallingFile] = useState<string | null>(null);
  const visibleFiles = filterUserVisibleArtifacts(
    files,
    artifacts.length > 0 ? artifacts : files,
  );

  const handleClick = useCallback(
    (filepath: string) => {
      selectArtifact(filepath);
      openArtifactPanel();
    },
    [openArtifactPanel, selectArtifact],
  );

  const handleInstallSkill = useCallback(
    async (e: React.MouseEvent, filepath: string) => {
      e.stopPropagation();
      e.preventDefault();

      if (installingFile) return;

      setInstallingFile(filepath);
      try {
        const result = await installSkill({
          thread_id: threadId,
          path: filepath,
        });
        if (result.success) {
          toast.success(result.message);
        } else {
          toast.error(result.message || "Failed to install skill");
        }
      } catch (error) {
        console.error("Failed to install skill:", error);
        if (error instanceof SkillRequestError && error.isAdminRequired) {
          toast.error(t.settings.skills.installAdminRequired);
        } else {
          toast.error("Failed to install skill");
        }
      } finally {
        setInstallingFile(null);
      }
    },
    [threadId, installingFile, t],
  );

  return (
    <ul
      className={cn(
        "cnooc-artifact-files flex w-full flex-col gap-3",
        className,
      )}
    >
      {visibleFiles.map((file) => (
        <li key={file} className="min-w-0">
          <Card
            data-artifact-filepath={file}
            className="cnooc-artifact-card relative cursor-pointer p-0"
            onClick={() => handleClick(file)}
          >
            <CardHeader className="cnooc-artifact-card-header gap-2 p-4">
              <CardTitle className="cnooc-artifact-summary flex min-w-0 items-center gap-3 leading-relaxed">
                <span className="cnooc-artifact-icon flex size-10 shrink-0 items-center justify-center rounded-lg">
                  {getFileIcon(file, "size-5")}
                </span>
                <span className="min-w-0">
                  <span
                    className="cnooc-artifact-name block min-w-0 [overflow-wrap:anywhere]"
                    title={getFileName(file)}
                  >
                    {getFileName(file)}
                  </span>
                  <span className="cnooc-artifact-kind text-muted-foreground block text-xs font-normal">
                    {getFileExtensionDisplayName(file)}
                  </span>
                </span>
              </CardTitle>
              <CardAction className="cnooc-artifact-actions flex w-full min-w-0 flex-wrap items-center justify-end gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`${t.common.preview} ${getFileName(file)}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    handleClick(file);
                  }}
                >
                  <EyeIcon />
                  <span className="cnooc-artifact-action-label">
                    {t.common.preview}
                  </span>
                </Button>
                {file.endsWith(".skill") && isAdmin && (
                  <Button
                    variant="ghost"
                    disabled={installingFile === file}
                    onClick={(e) => handleInstallSkill(e, file)}
                  >
                    {installingFile === file ? (
                      <LoaderIcon className="size-4 animate-spin" />
                    ) : (
                      <PackageIcon className="size-4" />
                    )}
                    {t.common.install}
                  </Button>
                )}
                <Button variant="outline" size="sm" asChild>
                  <a
                    href={urlOfArtifact({
                      filepath: file,
                      threadId: threadId,
                      download: true,
                    })}
                    aria-label={`${t.common.download} ${getFileName(file)}`}
                    title={t.common.download}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <DownloadIcon className="size-4" />
                    <span className="cnooc-artifact-action-label">
                      {t.common.download}
                    </span>
                  </a>
                </Button>
              </CardAction>
            </CardHeader>
          </Card>
        </li>
      ))}
    </ul>
  );
}
