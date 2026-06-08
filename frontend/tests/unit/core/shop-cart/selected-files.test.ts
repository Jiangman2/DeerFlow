import assert from "node:assert/strict";

import { test } from "vitest";

const { parseShopCartSelectedFiles } = await import(
  new URL("../../../../src/core/shop-cart/selected-files.ts", import.meta.url)
    .href
);

void test("parses string ID arrays", () => {
  const result = parseShopCartSelectedFiles(["a", "b"]);

  assert.equal(result.ok, true);
  assert.deepEqual(result.files, [{ fileId: "a" }, { fileId: "b" }]);
});

void test("parses fileId and fileName object arrays", () => {
  const result = parseShopCartSelectedFiles([
    { fileId: "a", fileName: "日报.pdf" },
    { fileId: "b", fileName: "勘探.xlsx" },
  ]);

  assert.equal(result.ok, true);
  assert.deepEqual(result.files, [
    { fileId: "a", fileName: "日报.pdf" },
    { fileId: "b", fileName: "勘探.xlsx" },
  ]);
});

void test("parses id and name object arrays", () => {
  const result = parseShopCartSelectedFiles([{ id: "a", name: "井筒.xlsx" }]);

  assert.equal(result.ok, true);
  assert.deepEqual(result.files, [{ fileId: "a", fileName: "井筒.xlsx" }]);
});

void test("rejects unrecognized payloads", () => {
  const result = parseShopCartSelectedFiles([{ bad: "shape" }]);

  assert.equal(result.ok, false);
  assert.equal(result.reason, "invalid_payload");
});
