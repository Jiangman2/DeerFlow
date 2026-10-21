/** Browser batch guard, not a server quota or request-rate limit. */
export const KNOWLEDGE_UPLOAD_BATCH_LIMIT = 10

export function isKnowledgeUploadBatchTooLarge(count: number): boolean {
  return count > KNOWLEDGE_UPLOAD_BATCH_LIMIT
}

/** Visible before selection; unlike the rejection message, this is not an error. */
export function knowledgeUploadBatchHint(locale: string): string {
  if (locale.toLowerCase().startsWith('zh')) {
    return `每批最多上传 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件。选择文件、拖拽、文件夹及子文件夹中的文件合并计数。`
  }
  return `Upload at most ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files per batch. Selected, dropped, and folder/subfolder files count together.`
}

export function knowledgeUploadMenuLimit(locale: string, fromFolder: boolean): string {
  if (locale.toLowerCase().startsWith('zh')) {
    return fromFolder
      ? `（含子文件夹，共最多 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件）`
      : `（每批最多 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件）`
  }
  return fromFolder
    ? ` (max ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files, including subfolders)`
    : ` (max ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files per batch)`
}

export function knowledgeUploadSelectionHint(count: number, locale: string): string {
  const remaining = Math.max(0, KNOWLEDGE_UPLOAD_BATCH_LIMIT - count)
  if (locale.toLowerCase().startsWith('zh')) {
    return `已选 ${count}/${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件，还可添加 ${remaining} 个。`
  }
  return `${count}/${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files selected; ${remaining} more can be added.`
}

export function knowledgeUploadBatchLimitMessage(count: number, locale: string): string {
  if (locale.toLowerCase().startsWith('zh')) {
    return `每次最多上传 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件，当前共 ${count} 个。文件夹及子文件夹中的文件合并计数，请减少文件后重试。`
  }
  return `Upload at most ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files per batch (${count} selected). Files in folders and subfolders count together. Please select fewer files.`
}
