const WORKBOOK_XML_PATH = "xl/workbook.xml";
const XLSX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

function readUint16(view: DataView, offset: number) {
  return view.getUint16(offset, true);
}

function readUint32(view: DataView, offset: number) {
  return view.getUint32(offset, true);
}

function decodeXmlEntities(value: string) {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function sanitizeFilename(value: string) {
  const sanitized = value.replace(/[\\/:*?"<>|]+/g, "_").trim();
  return sanitized || undefined;
}

function findEndOfCentralDirectory(view: DataView) {
  const minOffset = Math.max(0, view.byteLength - 0xffff - 22);
  for (let offset = view.byteLength - 22; offset >= minOffset; offset -= 1) {
    if (readUint32(view, offset) === 0x06054b50) {
      return offset;
    }
  }
  return undefined;
}

function inflateRaw(data: Uint8Array) {
  const DecompressionStreamCtor = globalThis.DecompressionStream;
  if (!DecompressionStreamCtor) {
    return undefined;
  }

  const buffer = new ArrayBuffer(data.byteLength);
  new Uint8Array(buffer).set(data);

  return new Response(
    new Blob([buffer])
      .stream()
      .pipeThrough(new DecompressionStreamCtor("deflate-raw")),
  ).arrayBuffer();
}

async function readZipEntry(
  buffer: ArrayBuffer,
  filename: string,
): Promise<Uint8Array | undefined> {
  const view = new DataView(buffer);
  const decoder = new TextDecoder();
  const eocdOffset = findEndOfCentralDirectory(view);
  if (eocdOffset === undefined) {
    return undefined;
  }

  const centralDirectorySize = readUint32(view, eocdOffset + 12);
  const centralDirectoryOffset = readUint32(view, eocdOffset + 16);
  let offset = centralDirectoryOffset;
  const endOffset = centralDirectoryOffset + centralDirectorySize;

  while (offset < endOffset && readUint32(view, offset) === 0x02014b50) {
    const compressionMethod = readUint16(view, offset + 10);
    const compressedSize = readUint32(view, offset + 20);
    const uncompressedSize = readUint32(view, offset + 24);
    const fileNameLength = readUint16(view, offset + 28);
    const extraFieldLength = readUint16(view, offset + 30);
    const fileCommentLength = readUint16(view, offset + 32);
    const localHeaderOffset = readUint32(view, offset + 42);
    const nameStart = offset + 46;
    const name = decoder.decode(
      new Uint8Array(buffer, nameStart, fileNameLength),
    );

    if (name === filename) {
      const localFileNameLength = readUint16(view, localHeaderOffset + 26);
      const localExtraFieldLength = readUint16(view, localHeaderOffset + 28);
      const dataStart =
        localHeaderOffset + 30 + localFileNameLength + localExtraFieldLength;
      const compressedData = new Uint8Array(buffer, dataStart, compressedSize);

      if (compressionMethod === 0) {
        return compressedData;
      }
      if (compressionMethod === 8) {
        const inflated = await inflateRaw(compressedData);
        return inflated
          ? new Uint8Array(inflated, 0, uncompressedSize)
          : undefined;
      }
      return undefined;
    }

    offset += 46 + fileNameLength + extraFieldLength + fileCommentLength;
  }

  return undefined;
}

export function parseFirstWorkbookSheetName(workbookXml: string) {
  const match = /<sheet\b[^>]*\bname=(["'])(.*?)\1/i.exec(workbookXml);
  const sheetName = match?.[2];
  return sheetName ? sanitizeFilename(decodeXmlEntities(sheetName)) : undefined;
}

export async function readFirstXlsxSheetName(blob: Blob) {
  const buffer = await blob.arrayBuffer();
  const workbookXml = await readZipEntry(buffer, WORKBOOK_XML_PATH);
  if (!workbookXml) {
    return undefined;
  }

  return parseFirstWorkbookSheetName(new TextDecoder().decode(workbookXml));
}

export async function convertShopCartBlobToXlsxFile(blob: Blob) {
  const sheetName = await readFirstXlsxSheetName(blob);
  if (!sheetName) {
    return undefined;
  }

  return new File([blob], `${sheetName}.xlsx`, {
    type: XLSX_MEDIA_TYPE,
  });
}
