export function knowledgeFilesFromInput(fileList: FileList | null): File[] {
  return Array.from(fileList ?? []);
}
