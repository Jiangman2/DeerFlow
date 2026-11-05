/** Browser batch guard only; never a server authorization/quota boundary. */
export const KNOWLEDGE_UPLOAD_BATCH_LIMIT = 10

export function isKnowledgeUploadBatchTooLarge(count: number, isSystemAdmin: boolean = false): boolean {
  return isSystemAdmin !== true && count > KNOWLEDGE_UPLOAD_BATCH_LIMIT
}

export function knowledgeUploadBatchHint(locale: string, isSystemAdmin: boolean = false): string {
  if (isSystemAdmin === true) {
    return locale.toLowerCase().startsWith('zh')
      ? '系统管理员不受每批 10 个文件的数量限制；文件类型、大小及上传权限限制仍然适用。'
      : 'System administrators are exempt from the 10-file batch limit. File type, size and upload permissions still apply.'
  }
  if (locale.toLowerCase().startsWith('zh')) {
    return `每批最多上传 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件。选择文件、拖拽、文件夹及子文件夹中的文件合并计数。`
  }
  return `Upload at most ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files per batch. Selected, dropped, and folder/subfolder files count together.`
}

export function knowledgeUploadMenuLimit(locale: string, fromFolder: boolean, isSystemAdmin: boolean = false): string {
  if (isSystemAdmin === true) {
    return locale.toLowerCase().startsWith('zh')
      ? '（管理员免单批数量限制）'
      : ' (administrator: no batch count limit)'
  }
  if (locale.toLowerCase().startsWith('zh')) {
    return fromFolder
      ? `（含子文件夹，共最多 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件）`
      : `（每批最多 ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} 个文件）`
  }
  return fromFolder
    ? ` (max ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files, including subfolders)`
    : ` (max ${KNOWLEDGE_UPLOAD_BATCH_LIMIT} files per batch)`
}

export function knowledgeUploadSelectionHint(count: number, locale: string, isSystemAdmin: boolean = false): string {
  if (isSystemAdmin === true) {
    return locale.toLowerCase().startsWith('zh')
      ? `已选 ${count} 个文件（管理员免单批数量限制）。`
      : `${count} files selected (administrator: no batch count limit).`
  }
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
