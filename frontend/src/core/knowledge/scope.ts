import type { KnowledgeFile } from "./types";

export interface KnowledgeScopeOption {
  type: "folder" | "file";
  path: string;
  label: string;
  level: number;
  filePaths: string[];
}

const MAX_KNOWLEDGE_SCOPE_OPTIONS = 80;

function normalizeKnowledgePath(value: string | null | undefined): string {
  return (value ?? "")
    .replaceAll("\\", "/")
    .split("/")
    .map((part) => part.trim())
    .filter(Boolean)
    .join("/");
}

function isPathInsideFolder(filePath: string, folderPath: string): boolean {
  return filePath === folderPath || filePath.startsWith(`${folderPath}/`);
}

function knowledgeFilePath(file: KnowledgeFile): string {
  return normalizeKnowledgePath(file.display_path || file.filename);
}

export function buildKnowledgeScopeOptions(
  files: KnowledgeFile[],
): KnowledgeScopeOption[] {
  const folderFiles = new Map<string, Set<string>>();
  const fileOptions = new Map<string, KnowledgeScopeOption>();

  for (const file of files) {
    const filePath = knowledgeFilePath(file);
    if (!filePath) continue;

    const folderPath = normalizeKnowledgePath(file.folder_path);
    const folderParts = folderPath.split("/").filter(Boolean);
    for (let index = 0; index < folderParts.length; index += 1) {
      const path = folderParts.slice(0, index + 1).join("/");
      const paths = folderFiles.get(path) ?? new Set<string>();
      paths.add(filePath);
      folderFiles.set(path, paths);
    }

    fileOptions.set(filePath, {
      type: "file",
      path: filePath,
      label: file.filename,
      level: folderParts.length,
      filePaths: [filePath],
    });
  }

  const folderOptions: KnowledgeScopeOption[] = Array.from(folderFiles.entries())
    .map(([path, paths]) => ({
      type: "folder" as const,
      path,
      label: path.split("/").at(-1) ?? path,
      level: Math.max(0, path.split("/").length - 1),
      filePaths: Array.from(paths).sort((a, b) => a.localeCompare(b)),
    }));

  return [...folderOptions, ...fileOptions.values()]
    .sort((a, b) => {
      const pathOrder = a.path.localeCompare(b.path);
      if (pathOrder !== 0) return pathOrder;
      if (a.type === b.type) return 0;
      return a.type === "folder" ? -1 : 1;
    })
    .slice(0, MAX_KNOWLEDGE_SCOPE_OPTIONS);
}

export function expandKnowledgeScopePaths(
  scopePaths: readonly string[],
  options: readonly KnowledgeScopeOption[],
): Set<string> {
  const optionByPath = new Map(options.map((option) => [option.path, option]));
  const selectedFiles = new Set<string>();

  for (const rawPath of scopePaths) {
    const path = normalizeKnowledgePath(rawPath);
    if (!path) continue;

    const option = optionByPath.get(path);
    if (option?.type === "folder") {
      for (const filePath of option.filePaths) {
        selectedFiles.add(filePath);
      }
      continue;
    }
    if (option?.type === "file") {
      selectedFiles.add(option.path);
      continue;
    }

    let matchedKnownPath = false;
    for (const candidate of options) {
      if (candidate.type === "file" && isPathInsideFolder(candidate.path, path)) {
        selectedFiles.add(candidate.path);
        matchedKnownPath = true;
      }
    }
    if (!matchedKnownPath) {
      selectedFiles.add(path);
    }
  }

  return selectedFiles;
}

export function isKnowledgeScopeOptionChecked(
  option: KnowledgeScopeOption,
  selectedFilePaths: ReadonlySet<string>,
): boolean {
  if (option.type === "file") {
    return selectedFilePaths.has(option.path);
  }
  return (
    option.filePaths.length > 0 &&
    option.filePaths.every((path) => selectedFilePaths.has(path))
  );
}

export function updateKnowledgeScopeSelection(
  currentPaths: readonly string[],
  option: KnowledgeScopeOption,
  checked: boolean,
  options: readonly KnowledgeScopeOption[],
): string[] {
  const selectedFiles = expandKnowledgeScopePaths(currentPaths, options);

  if (option.type === "folder") {
    for (const filePath of option.filePaths) {
      if (checked) selectedFiles.add(filePath);
      else selectedFiles.delete(filePath);
    }
  } else if (checked) {
    selectedFiles.add(option.path);
  } else {
    selectedFiles.delete(option.path);
  }

  const result: string[] = [];
  const remainingFiles = new Set(selectedFiles);
  const folderOptions = options
    .filter((item) => item.type === "folder")
    .sort((a, b) => a.level - b.level || a.path.localeCompare(b.path));

  for (const folder of folderOptions) {
    if (
      folder.filePaths.length > 0 &&
      folder.filePaths.every((filePath) => remainingFiles.has(filePath))
    ) {
      result.push(folder.path);
      for (const filePath of folder.filePaths) {
        remainingFiles.delete(filePath);
      }
    }
  }

  for (const file of options.filter((item) => item.type === "file")) {
    if (remainingFiles.has(file.path)) {
      result.push(file.path);
      remainingFiles.delete(file.path);
    }
  }

  for (const filePath of Array.from(remainingFiles).sort((a, b) => a.localeCompare(b))) {
    result.push(filePath);
  }

  return result;
}
