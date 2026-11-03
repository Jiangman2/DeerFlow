export function parseContentDispositionFilename(
  contentDisposition: string | null | undefined,
): string | undefined {
  if (!contentDisposition) {
    return undefined;
  }

  const filenameStarMatch = /filename\*=([^;]+)/i.exec(contentDisposition);
  if (filenameStarMatch?.[1]) {
    const value = filenameStarMatch[1].trim().replace(/^"|"$/g, "");
    const encoded = value.includes("''")
      ? value.split("''").slice(1).join("''")
      : value;
    try {
      const decoded = decodeURIComponent(encoded);
      if (decoded.trim()) {
        return decoded.trim();
      }
    } catch {
      if (encoded.trim()) {
        return encoded.trim();
      }
    }
  }

  const filenameMatch = /filename=([^;]+)/i.exec(contentDisposition);
  if (filenameMatch?.[1]) {
    const value = filenameMatch[1].trim().replace(/^"|"$/g, "");
    return value.trim() || undefined;
  }

  return undefined;
}

export function chooseShopCartFilename({
  contentDisposition,
  fileId,
  iframeFileName,
}: {
  contentDisposition?: string | null;
  fileId: string;
  iframeFileName?: string;
}) {
  return (
    parseContentDispositionFilename(contentDisposition) ??
    iframeFileName?.trim() ??
    `数据湖文件-${fileId}`
  );
}
