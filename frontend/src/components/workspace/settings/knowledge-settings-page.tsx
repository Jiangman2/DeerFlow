"use client";

import {
  ChevronDownIcon,
  ChevronRightIcon,
  ClipboardCopyIcon,
  ClipboardPasteIcon,
  Edit3Icon,
  FileTextIcon,
  FolderIcon,
  FolderOpenIcon,
  FolderPlusIcon,
  Loader2Icon,
  RefreshCwIcon,
  ScissorsIcon,
  SearchIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  useCopyKnowledgeFile,
  useCreateKnowledgeFolder,
  useDeleteKnowledgeFile,
  useDeleteKnowledgeFolder,
  useKnowledgeFiles,
  useMoveKnowledgeFile,
  useMoveKnowledgeFolder,
  useReindexKnowledgeFile,
  useRenameKnowledgeFolder,
  useSearchKnowledge,
  useUploadKnowledgeFiles,
} from "@/core/knowledge";
import type { KnowledgeFile, KnowledgeFolder } from "@/core/knowledge";
import { cn } from "@/lib/utils";

import { SettingsSection } from "./settings-section";

interface KnowledgeTreeNode {
  name: string;
  path: string;
  folders: Map<string, KnowledgeTreeNode>;
  files: KnowledgeFile[];
  explicit: boolean;
}

type Selection =
  | { kind: "file"; id: string }
  | { kind: "folder"; path: string }
  | null;

type ClipboardItem =
  | { mode: "copy" | "cut"; kind: "file"; id: string }
  | { mode: "cut"; kind: "folder"; path: string };

function formatBytes(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(
    Math.floor(Math.log(size) / Math.log(1024)),
    units.length - 1,
  );
  return `${(size / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusVariant(status: string): "default" | "secondary" | "destructive" {
  if (status === "indexed") return "default";
  if (status === "failed") return "destructive";
  return "secondary";
}

function normalizeFolderPath(value: string): string {
  return value
    .replaceAll("\\", "/")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean)
    .join("/");
}

function basename(path: string): string {
  const normalized = normalizeFolderPath(path);
  return normalized.split("/").filter(Boolean).at(-1) ?? "";
}

function joinPath(parent: string, name: string): string {
  const safeParent = normalizeFolderPath(parent);
  const safeName = normalizeFolderPath(name);
  if (!safeParent) return safeName;
  if (!safeName) return safeParent;
  return `${safeParent}/${safeName}`;
}

function createNode(name: string, path: string, explicit = false): KnowledgeTreeNode {
  return { name, path, folders: new Map(), files: [], explicit };
}

function ensureNode(root: KnowledgeTreeNode, folderPath: string, explicit = false) {
  const parts = normalizeFolderPath(folderPath).split("/").filter(Boolean);
  let current = root;
  for (const part of parts) {
    const path = current.path ? `${current.path}/${part}` : part;
    let next = current.folders.get(part);
    if (!next) {
      next = createNode(part, path);
      current.folders.set(part, next);
    }
    current = next;
  }
  current.explicit = current.explicit || explicit;
  return current;
}

function buildKnowledgeTree(
  files: KnowledgeFile[],
  folders: KnowledgeFolder[],
): KnowledgeTreeNode {
  const root = createNode("知识库", "");
  for (const folder of folders) {
    ensureNode(root, folder.path, true);
  }
  for (const file of files) {
    ensureNode(root, file.folder_path).files.push(file);
  }
  return root;
}

function countTreeFiles(node: KnowledgeTreeNode): number {
  let count = node.files.length;
  for (const child of node.folders.values()) {
    count += countTreeFiles(child);
  }
  return count;
}

function treeSize(node: KnowledgeTreeNode): number {
  let size = node.files.reduce((total, file) => total + file.size, 0);
  for (const child of node.folders.values()) {
    size += treeSize(child);
  }
  return size;
}

function sortedFolders(node: KnowledgeTreeNode[]): KnowledgeTreeNode[];
function sortedFolders(node: KnowledgeTreeNode): KnowledgeTreeNode[];
function sortedFolders(node: KnowledgeTreeNode | KnowledgeTreeNode[]) {
  const folders = Array.isArray(node) ? node : Array.from(node.folders.values());
  return [...folders].sort((a, b) => a.name.localeCompare(b.name));
}

function sortedFiles(files: KnowledgeFile[]): KnowledgeFile[] {
  return [...files].sort((a, b) => a.filename.localeCompare(b.filename));
}

function selectedTargetFolder(selection: Selection): string {
  return selection?.kind === "folder" ? selection.path : "";
}

function summarizeUpload(files: KnowledgeFile[]): {
  indexed: number;
  failed: number;
  chunks: number;
  message: string;
} {
  const failed = files.filter((file) => file.status === "failed").length;
  const indexed = files.filter((file) => file.status === "indexed").length;
  const chunks = files.reduce((total, file) => total + file.chunk_count, 0);
  return {
    indexed,
    failed,
    chunks,
    message: `上传完成：${indexed} 个已索引，${failed} 个失败，共 ${chunks} 个知识片段`,
  };
}

function KnowledgeFileRow({
  file,
  level,
  selected,
  onSelect,
  onDelete,
  onRename,
  onReindex,
  onDragStart,
  isBusy,
}: {
  file: KnowledgeFile;
  level: number;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onRename: () => void;
  onReindex: () => void;
  onDragStart: (event: DragEvent<HTMLDivElement>) => void;
  isBusy: boolean;
}) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onClick={onSelect}
      className={cn(
        "grid min-h-12 cursor-default grid-cols-[minmax(220px,1fr)_96px_96px_132px_120px] items-center gap-3 border-t px-3 py-2 text-sm",
        selected ? "bg-cyan-50" : "hover:bg-slate-50",
      )}
      style={{ paddingLeft: `${level * 20 + 12}px` }}
    >
      <div className="flex min-w-0 items-center gap-2">
        <FileTextIcon className="size-4 shrink-0 text-slate-500" />
        <div className="min-w-0">
          <div className="truncate font-medium">{file.filename}</div>
          {file.status === "indexed" && (
            <div className="truncate text-xs text-slate-500">
              已处理 {file.chunk_count} 个知识片段
            </div>
          )}
          {file.error && (
            <div className="text-destructive truncate text-xs">{file.error}</div>
          )}
        </div>
      </div>
      <span className="tabular-nums text-slate-600">{formatBytes(file.size)}</span>
      <Badge className="w-fit" variant={statusVariant(file.status)}>
        {file.status}
      </Badge>
      <span className="text-slate-500">{formatDate(file.updated_at)}</span>
      <div className="flex justify-end gap-1">
        <Button size="icon-sm" variant="ghost" title="重命名" onClick={(event) => { event.stopPropagation(); onRename(); }}>
          <Edit3Icon className="size-4" />
        </Button>
        <Button size="icon-sm" variant="ghost" title="重新索引" disabled={isBusy} onClick={(event) => { event.stopPropagation(); onReindex(); }}>
          {isBusy ? <Loader2Icon className="size-4 animate-spin" /> : <RefreshCwIcon className="size-4" />}
        </Button>
        <Button size="icon-sm" variant="ghost" title="删除" onClick={(event) => { event.stopPropagation(); onDelete(); }}>
          <Trash2Icon className="size-4" />
        </Button>
      </div>
    </div>
  );
}

function KnowledgeFolderRow({
  node,
  level,
  expandedFolders,
  selected,
  onSelect,
  onToggle,
  onDelete,
  onRename,
  onDropItem,
  onDragStart,
  renderFile,
  renderFolder,
}: {
  node: KnowledgeTreeNode;
  level: number;
  expandedFolders: Set<string>;
  selected: boolean;
  onSelect: () => void;
  onToggle: () => void;
  onDelete: () => void;
  onRename: () => void;
  onDropItem: (path: string) => void;
  onDragStart: (event: DragEvent<HTMLDivElement>) => void;
  renderFile: (file: KnowledgeFile, level: number) => ReactNode;
  renderFolder: (node: KnowledgeTreeNode, level: number) => ReactNode;
}) {
  const isOpen = expandedFolders.has(node.path);
  const fileCount = countTreeFiles(node);

  return (
    <div>
      <div
        draggable
        onDragStart={onDragStart}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onDropItem(node.path);
        }}
        onClick={onSelect}
        className={cn(
          "grid min-h-12 cursor-default grid-cols-[minmax(220px,1fr)_96px_96px_132px_120px] items-center gap-3 border-t px-3 py-2 text-sm",
          selected ? "bg-cyan-50" : "hover:bg-slate-50",
        )}
        style={{ paddingLeft: `${level * 20 + 8}px` }}
      >
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            className="rounded p-0.5 hover:bg-slate-100"
            onClick={(event) => {
              event.stopPropagation();
              onToggle();
            }}
          >
            {isOpen ? (
              <ChevronDownIcon className="size-4 text-slate-500" />
            ) : (
              <ChevronRightIcon className="size-4 text-slate-500" />
            )}
          </button>
          {isOpen ? (
            <FolderOpenIcon className="size-4 shrink-0 text-amber-600" />
          ) : (
            <FolderIcon className="size-4 shrink-0 text-amber-600" />
          )}
          <div className="min-w-0">
            <div className="truncate font-medium">{node.name}</div>
            <div className="truncate text-xs text-slate-500">{node.path}</div>
          </div>
        </div>
        <span className="tabular-nums text-slate-600">
          {fileCount} 个文件
        </span>
        <Badge className="w-fit" variant="secondary">
          文件夹
        </Badge>
        <span className="text-slate-500">{formatBytes(treeSize(node))}</span>
        <div className="flex justify-end gap-1">
          <Button size="icon-sm" variant="ghost" title="重命名" onClick={(event) => { event.stopPropagation(); onRename(); }}>
            <Edit3Icon className="size-4" />
          </Button>
          <Button size="icon-sm" variant="ghost" title="删除" onClick={(event) => { event.stopPropagation(); onDelete(); }}>
            <Trash2Icon className="size-4" />
          </Button>
        </div>
      </div>
      {isOpen && (
        <div>
          {sortedFolders(node).map((child) => renderFolder(child, level + 1))}
          {sortedFiles(node.files).map((file) => renderFile(file, level + 1))}
        </div>
      )}
    </div>
  );
}

export function KnowledgeSettingsPage() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [query, setQuery] = useState("");
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
    () => new Set(),
  );
  const [selection, setSelection] = useState<Selection>(null);
  const [clipboard, setClipboard] = useState<ClipboardItem | null>(null);
  const [draggedItem, setDraggedItem] = useState<ClipboardItem | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);

  const { files, folders, isLoading, error } = useKnowledgeFiles();
  const uploadMutation = useUploadKnowledgeFiles();
  const deleteMutation = useDeleteKnowledgeFile();
  const deleteFolderMutation = useDeleteKnowledgeFolder();
  const createFolderMutation = useCreateKnowledgeFolder();
  const moveFileMutation = useMoveKnowledgeFile();
  const copyFileMutation = useCopyKnowledgeFile();
  const renameFolderMutation = useRenameKnowledgeFolder();
  const moveFolderMutation = useMoveKnowledgeFolder();
  const reindexMutation = useReindexKnowledgeFile();
  const searchMutation = useSearchKnowledge();

  const tree = useMemo(() => buildKnowledgeTree(files, folders), [files, folders]);
  const rootFolders = useMemo(() => sortedFolders(tree), [tree]);
  const rootFiles = useMemo(() => sortedFiles(tree.files), [tree.files]);
  const selectedFile = useMemo(
    () => files.find((file) => selection?.kind === "file" && file.id === selection.id),
    [files, selection],
  );
  const selectedFolderPath = selectedTargetFolder(selection);
  const selectedSearchScopePaths = useMemo(() => {
    if (selectedFile) return [selectedFile.display_path];
    if (selection?.kind === "folder") return [selection.path];
    return [];
  }, [selectedFile, selection]);
  const isMutating =
    uploadMutation.isPending ||
    deleteMutation.isPending ||
    deleteFolderMutation.isPending ||
    createFolderMutation.isPending ||
    moveFileMutation.isPending ||
    copyFileMutation.isPending ||
    renameFolderMutation.isPending ||
    moveFolderMutation.isPending;

  useEffect(() => {
    const folderInput = folderInputRef.current;
    if (!folderInput) return;
    folderInput.setAttribute("webkitdirectory", "");
    folderInput.setAttribute("directory", "");
  }, []);

  useEffect(() => {
    setExpandedFolders((current) => {
      const next = new Set(current);
      for (const folder of rootFolders) {
        next.add(folder.path);
      }
      return next;
    });
  }, [rootFolders]);

  function expandPath(path: string) {
    const parts = normalizeFolderPath(path).split("/").filter(Boolean);
    setExpandedFolders((current) => {
      const next = new Set(current);
      let cursor = "";
      for (const part of parts) {
        cursor = cursor ? `${cursor}/${part}` : part;
        next.add(cursor);
      }
      return next;
    });
  }

  async function handleSelectedFiles(fileList: FileList | null) {
    const selected = Array.from(fileList ?? []);
    if (selected.length === 0) return;
    setUploadMessage(`正在上传 ${selected.length} 个文件...`);
    try {
      const result = await uploadMutation.mutateAsync(selected);
      const summary = summarizeUpload(result.files);
      for (const file of result.files) {
        if (file.folder_path) expandPath(file.folder_path);
      }
      setUploadMessage(summary.message);
      if (summary.failed > 0) {
        toast.warning(summary.message);
      } else {
        toast.success(summary.message);
      }
    } catch (err) {
      setUploadMessage("上传失败");
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files;
    event.target.value = "";
    await handleSelectedFiles(selected);
  }

  function toggleFolder(path: string) {
    setExpandedFolders((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function createFolder() {
    const parent = selectedFolderPath;
    const name = window.prompt("新建文件夹名称", parent ? `${parent}/新建文件夹` : "新建文件夹");
    if (!name) return;
    const path = normalizeFolderPath(name.includes("/") ? name : joinPath(parent, name));
    if (!path) return;
    try {
      await createFolderMutation.mutateAsync({ path });
      expandPath(path);
      setSelection({ kind: "folder", path });
      toast.success("文件夹已创建");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function renameSelected() {
    if (!selection) return;
    if (selection.kind === "file") {
      await renameFileById(selection.id);
    } else {
      await renameFolderByPath(selection.path);
    }
  }

  async function renameFileById(fileId: string) {
    const file = files.find((item) => item.id === fileId);
    if (!file) return;
    try {
      const name = window.prompt("文件名称", file.filename);
      if (!name || name === file.filename) return;
      await moveFileMutation.mutateAsync({
        fileId: file.id,
        target_folder_path: file.folder_path,
        target_filename: name,
      });
      toast.success("文件已重命名");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function renameFolderByPath(folderPath: string) {
    try {
      const name = window.prompt("文件夹名称", basename(folderPath));
      if (!name || name === basename(folderPath)) return;
      await renameFolderMutation.mutateAsync({
        folderPath,
        new_name: name,
      });
      toast.success("文件夹已重命名");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteSelected() {
    if (!selection) return;
    if (selection.kind === "file") {
      await deleteFileById(selection.id);
    } else {
      await deleteFolderByPath(selection.path);
    }
  }

  async function deleteFileById(fileId: string) {
    const file = files.find((item) => item.id === fileId);
    if (!file || !window.confirm(`删除文件「${file.filename}」？`)) return;
    try {
      await deleteMutation.mutateAsync(file.id);
      setSelection(null);
      toast.success("文件已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteFolderByPath(folderPath: string) {
    if (!window.confirm(`删除文件夹「${folderPath}」及其所有文件？`)) return;
    try {
      await deleteFolderMutation.mutateAsync(folderPath);
      setSelection(null);
      toast.success("文件夹已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function copySelected() {
    if (selection?.kind !== "file") {
      toast.info("当前仅支持复制文件；文件夹可剪切移动");
      return;
    }
    setClipboard({ mode: "copy", kind: "file", id: selection.id });
    toast.success("已复制到知识库剪贴板");
  }

  function cutSelected() {
    if (!selection) return;
    setClipboard(
      selection.kind === "file"
        ? { mode: "cut", kind: "file", id: selection.id }
        : { mode: "cut", kind: "folder", path: selection.path },
    );
    toast.success("已剪切到知识库剪贴板");
  }

  async function applyClipboard(targetFolderPath: string, item = clipboard) {
    if (!item) return;
    try {
      if (item.kind === "file") {
        if (item.mode === "copy") {
          await copyFileMutation.mutateAsync({
            fileId: item.id,
            target_folder_path: targetFolderPath,
          });
          toast.success("文件已复制");
        } else {
          await moveFileMutation.mutateAsync({
            fileId: item.id,
            target_folder_path: targetFolderPath,
          });
          setClipboard(null);
          toast.success("文件已移动");
        }
      } else {
        const targetPath = joinPath(targetFolderPath, basename(item.path));
        await moveFolderMutation.mutateAsync({
          folderPath: item.path,
          target_path: targetPath,
        });
        setClipboard(null);
        expandPath(targetPath);
        toast.success("文件夹已移动");
      }
      if (targetFolderPath) expandPath(targetFolderPath);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function reindexFile(fileId: string) {
    try {
      await reindexMutation.mutateAsync(fileId);
      toast.success("重新索引完成");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleSearch() {
    const trimmed = query.trim();
    if (!trimmed) return;
    try {
      await searchMutation.mutateAsync({
        query: trimmed,
        limit: 8,
        scopePaths: selectedSearchScopePaths,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  function renderFile(file: KnowledgeFile, level: number) {
    return (
      <KnowledgeFileRow
        key={file.id}
        file={file}
        level={level}
        selected={selection?.kind === "file" && selection.id === file.id}
        onSelect={() => setSelection({ kind: "file", id: file.id })}
        onDelete={() => void deleteFileById(file.id)}
        onRename={() => void renameFileById(file.id)}
        onReindex={() => void reindexFile(file.id)}
        isBusy={reindexMutation.isPending && reindexMutation.variables === file.id}
        onDragStart={(event) => {
          const item: ClipboardItem = { mode: "cut", kind: "file", id: file.id };
          setDraggedItem(item);
          event.dataTransfer.setData("application/x-deer-knowledge", JSON.stringify(item));
          event.dataTransfer.effectAllowed = "move";
        }}
      />
    );
  }

  function renderFolder(node: KnowledgeTreeNode, level: number) {
    return (
      <KnowledgeFolderRow
        key={node.path}
        node={node}
        level={level}
        expandedFolders={expandedFolders}
        selected={selection?.kind === "folder" && selection.path === node.path}
        onSelect={() => setSelection({ kind: "folder", path: node.path })}
        onToggle={() => toggleFolder(node.path)}
        onDelete={() => void deleteFolderByPath(node.path)}
        onRename={() => void renameFolderByPath(node.path)}
        onDropItem={(target) => {
          const raw = eventFromDataTransfer() ?? draggedItem;
          void applyClipboard(target, raw);
          setDraggedItem(null);
        }}
        onDragStart={(event) => {
          const item: ClipboardItem = { mode: "cut", kind: "folder", path: node.path };
          setDraggedItem(item);
          event.dataTransfer.setData("application/x-deer-knowledge", JSON.stringify(item));
          event.dataTransfer.effectAllowed = "move";
        }}
        renderFile={renderFile}
        renderFolder={renderFolder}
      />
    );
  }

  function eventFromDataTransfer(): ClipboardItem | null {
    return draggedItem;
  }

  return (
    <div className="space-y-8">
      <SettingsSection
        title="个人知识库"
        description="上传后的文件会进入你的私有索引，可按文件夹重新归类；后续任务会自动检索相关片段。"
      >
        <div className="space-y-5">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileChange}
          />
          <input
            ref={folderInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileChange}
          />

          <div className="flex flex-wrap items-center gap-2 rounded-md border bg-white p-2">
            <Button type="button" onClick={() => fileInputRef.current?.click()} disabled={isMutating}>
              {uploadMutation.isPending ? <Loader2Icon className="size-4 animate-spin" /> : <UploadIcon className="size-4" />}
              上传文件
            </Button>
            <Button type="button" variant="outline" onClick={() => folderInputRef.current?.click()} disabled={isMutating}>
              <FolderIcon className="size-4" />
              上传文件夹
            </Button>
            <Button type="button" variant="outline" onClick={() => void createFolder()} disabled={isMutating}>
              <FolderPlusIcon className="size-4" />
              新建文件夹
            </Button>
            <Button type="button" variant="outline" onClick={copySelected} disabled={selection?.kind !== "file" || isMutating}>
              <ClipboardCopyIcon className="size-4" />
              复制
            </Button>
            <Button type="button" variant="outline" onClick={cutSelected} disabled={!selection || isMutating}>
              <ScissorsIcon className="size-4" />
              剪切
            </Button>
            <Button type="button" variant="outline" onClick={() => void applyClipboard(selectedFolderPath)} disabled={!clipboard || isMutating}>
              <ClipboardPasteIcon className="size-4" />
              粘贴到当前文件夹
            </Button>
            <Button type="button" variant="outline" onClick={() => void renameSelected()} disabled={!selection || isMutating}>
              <Edit3Icon className="size-4" />
              重命名
            </Button>
            <Button type="button" variant="outline" onClick={() => void deleteSelected()} disabled={!selection || isMutating}>
              <Trash2Icon className="size-4" />
              删除
            </Button>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-500">
            <span>
              {files.length} 个文件 / {folders.length} 个文件夹
              {selection?.kind === "folder" ? ` / 当前：${selection.path}` : ""}
              {selectedFile ? ` / 当前：${selectedFile.display_path}` : ""}
            </span>
            {uploadMessage && (
              <span className={uploadMutation.isPending ? "text-cyan-700" : ""}>
                {uploadMutation.isPending && <Loader2Icon className="mr-1 inline size-3 animate-spin" />}
                {uploadMessage}
              </span>
            )}
          </div>

          {error && (
            <Alert variant="destructive">
              <AlertDescription>
                {error instanceof Error ? error.message : String(error)}
              </AlertDescription>
            </Alert>
          )}

          <div
            className="overflow-hidden rounded-md border bg-white"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              void applyClipboard("", draggedItem);
              setDraggedItem(null);
            }}
          >
            <div className="grid grid-cols-[minmax(220px,1fr)_96px_96px_132px_120px] gap-3 bg-slate-50 px-3 py-2 text-xs font-medium text-slate-500">
              <span>名称</span>
              <span>大小/数量</span>
              <span>状态</span>
              <span>修改时间</span>
              <span className="text-right">操作</span>
            </div>
            {isLoading ? (
              <div className="flex items-center gap-2 px-4 py-8 text-sm text-slate-500">
                <Loader2Icon className="size-4 animate-spin" />
                正在加载文件
              </div>
            ) : files.length === 0 && folders.length === 0 ? (
              <div className="px-4 py-8 text-sm text-slate-500">
                暂无知识库文件。可以上传单个文件，也可以上传整个文件夹。
              </div>
            ) : (
              <div>
                {rootFolders.map((folder) => renderFolder(folder, 0))}
                {rootFiles.map((file) => renderFile(file, 0))}
              </div>
            )}
          </div>
        </div>
      </SettingsSection>

      <SettingsSection title="检索测试">
        <div className="space-y-4">
          <div className="flex gap-2">
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleSearch();
              }}
              placeholder="输入关键词"
            />
            <Button
              type="button"
              size="icon"
              title="搜索"
              aria-label="搜索"
              disabled={searchMutation.isPending || !query.trim()}
              onClick={() => void handleSearch()}
            >
              {searchMutation.isPending ? (
                <Loader2Icon className="size-4 animate-spin" />
              ) : (
                <SearchIcon className="size-4" />
              )}
            </Button>
          </div>
          <div className="text-xs text-slate-500">
            测试范围：
            {selectedSearchScopePaths.length > 0
              ? selectedSearchScopePaths[0]
              : "全部知识库"}
          </div>

          {searchMutation.data && (
            <div className="space-y-3">
              {searchMutation.data.results.length === 0 ? (
                <div className="text-sm text-slate-500">没有命中结果</div>
              ) : (
                searchMutation.data.results.map((result) => (
                  <div
                    key={result.chunk_id}
                    className={cn("rounded-md border p-3 text-sm")}
                  >
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span className="font-medium">{result.filename}</span>
                      {result.heading && (
                        <span className="text-slate-500">{result.heading}</span>
                      )}
                      <Badge variant="secondary">{result.score}</Badge>
                    </div>
                    <Textarea
                      readOnly
                      value={result.snippet}
                      className="min-h-20 resize-none text-sm"
                    />
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </SettingsSection>
    </div>
  );
}
