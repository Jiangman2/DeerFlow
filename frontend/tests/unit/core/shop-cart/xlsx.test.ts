import assert from "node:assert/strict";

import { test } from "vitest";

const { parseFirstWorkbookSheetName, readFirstXlsxSheetName } = await import(
  new URL("../../../../src/core/shop-cart/xlsx.ts", import.meta.url).href
);

const textEncoder = new TextEncoder();

function writeUint16(output: number[], value: number) {
  output.push(value & 0xff, (value >> 8) & 0xff);
}

function writeUint32(output: number[], value: number) {
  output.push(
    value & 0xff,
    (value >> 8) & 0xff,
    (value >> 16) & 0xff,
    (value >> 24) & 0xff,
  );
}

function createStoredZipFile(filename: string, content: string) {
  const filenameBytes = textEncoder.encode(filename);
  const contentBytes = textEncoder.encode(content);
  const output: number[] = [];

  writeUint32(output, 0x04034b50);
  writeUint16(output, 20);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint32(output, 0);
  writeUint32(output, contentBytes.byteLength);
  writeUint32(output, contentBytes.byteLength);
  writeUint16(output, filenameBytes.byteLength);
  writeUint16(output, 0);
  output.push(...filenameBytes, ...contentBytes);

  const centralDirectoryOffset = output.length;
  writeUint32(output, 0x02014b50);
  writeUint16(output, 20);
  writeUint16(output, 20);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint32(output, 0);
  writeUint32(output, contentBytes.byteLength);
  writeUint32(output, contentBytes.byteLength);
  writeUint16(output, filenameBytes.byteLength);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint32(output, 0);
  writeUint32(output, 0);
  output.push(...filenameBytes);

  const centralDirectorySize = output.length - centralDirectoryOffset;
  writeUint32(output, 0x06054b50);
  writeUint16(output, 0);
  writeUint16(output, 0);
  writeUint16(output, 1);
  writeUint16(output, 1);
  writeUint32(output, centralDirectorySize);
  writeUint32(output, centralDirectoryOffset);
  writeUint16(output, 0);

  return new Uint8Array(output);
}

void test("parses the first workbook sheet name", () => {
  assert.equal(
    parseFirstWorkbookSheetName(
      '<workbook><sheets><sheet name="日报&amp;统计" sheetId="1"/></sheets></workbook>',
    ),
    "日报&统计",
  );
});

void test("sanitizes sheet names for filenames", () => {
  assert.equal(
    parseFirstWorkbookSheetName('<sheet name="A/B:*?&quot;&lt;&gt;|" />'),
    "A_B_",
  );
});

void test("reads the first sheet name from an xlsx zip", async () => {
  const data = createStoredZipFile(
    "xl/workbook.xml",
    '<workbook><sheets><sheet name="常规随钻测井设备故障记录表" sheetId="1"/></sheets></workbook>',
  );
  assert.equal(
    await readFirstXlsxSheetName(new Blob([data])),
    "常规随钻测井设备故障记录表",
  );
});
