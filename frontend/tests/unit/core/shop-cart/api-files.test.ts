import assert from "node:assert/strict";

import { test } from "@rstest/core";

import {
  createNewShopCartFile,
  getFileTypeFromName,
  parseNewShopCartFile,
  parseNewShopCartListResponse,
} from "@/core/shop-cart/api-files";

const exampleItem = {
  id: "6b05b21ff9d7456c89c34db1826b71b0",
  fileId: "2059103245724418048",
  userId: "02024020509525572100000101661465",
  isComplete: 1,
  createDate: "2026-05-26 10:44:05",
  fileName: "report.xlsx",
  type: 0,
  ptCode: "06664ab15eacc886e1ea65eff62f3230",
  isStore: 0,
  dacCode: null,
  num: 1,
  remark: null,
  tipsInfo: null,
  extra: '{"ptName":"demo"}',
  errorInfo: null,
  shopType: 0,
};

void test("parses successful new shop cart list responses", () => {
  const result = parseNewShopCartListResponse({
    code: 0,
    msg: null,
    data: [exampleItem],
  });

  assert.equal(result.ok, true);
  assert.equal(result.files.length, 1);
  assert.equal(result.files[0]?.fileId, exampleItem.fileId);
  assert.equal(result.files[0]?.fileName, exampleItem.fileName);
});

void test("parses new shop cart list responses with null extra fields", () => {
  const result = parseNewShopCartListResponse({
    code: 0,
    msg: null,
    data: [{ ...exampleItem, extra: null }],
  });

  assert.equal(result.ok, true);
  assert.equal(result.files[0]?.extra, null);
});

void test("parses incomplete new shop cart list items with null download fields", () => {
  const incompleteItem = {
    ...exampleItem,
    id: "pending-file",
    fileId: null,
    isComplete: 0,
    num: null,
  };

  const result = parseNewShopCartListResponse({
    code: 0,
    msg: null,
    data: [exampleItem, incompleteItem],
  });

  assert.equal(result.ok, true);
  assert.equal(result.files.length, 2);
  assert.equal(result.files[1]?.isComplete, 0);
  assert.equal(result.files[1]?.fileId, null);
  assert.equal(result.files[1]?.num, null);
});

void test("rejects unsuccessful new shop cart list responses", () => {
  const result = parseNewShopCartListResponse({
    code: 500,
    msg: "failed",
    data: [],
  });

  assert.equal(result.ok, false);
});

void test("infers content types from file names", () => {
  assert.equal(
    getFileTypeFromName("report.xlsx"),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  assert.equal(getFileTypeFromName("survey.pdf"), "application/pdf");
  assert.equal(getFileTypeFromName("notes.txt"), "text/plain");
  assert.equal(getFileTypeFromName("unknown.bin"), "application/octet-stream");
});

void test("creates files using the original shop cart file name", async () => {
  const file = createNewShopCartFile(new Blob(["hello"]), {
    fileId: exampleItem.fileId,
    fileName: exampleItem.fileName,
  });

  assert.equal(file.name, exampleItem.fileName);
  assert.equal(
    file.type,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
  assert.equal(await file.text(), "hello");
});

void test("parses a single new shop cart file payload", () => {
  const result = parseNewShopCartFile(exampleItem);

  assert.equal(result.ok, true);
  assert.equal(result.file.fileId, exampleItem.fileId);
});
