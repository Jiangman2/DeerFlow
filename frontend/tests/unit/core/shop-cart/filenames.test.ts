import assert from "node:assert/strict";

import { test } from "vitest";

const { chooseShopCartFilename, parseContentDispositionFilename } =
  await import(
    new URL("../../../../src/core/shop-cart/filenames.ts", import.meta.url).href
  );

void test("parses content-disposition filename", () => {
  assert.equal(
    parseContentDispositionFilename('attachment; filename="井筒.xlsx"'),
    "井筒.xlsx",
  );
});

void test("parses RFC 5987 filename star", () => {
  assert.equal(
    parseContentDispositionFilename(
      "attachment; filename*=UTF-8''%E4%BA%95%E7%AD%92.xlsx",
    ),
    "井筒.xlsx",
  );
});

void test("chooses response filename before iframe filename", () => {
  assert.equal(
    chooseShopCartFilename({
      contentDisposition: 'attachment; filename="响应.pdf"',
      fileId: "abc",
      iframeFileName: "iframe.pdf",
    }),
    "响应.pdf",
  );
});

void test("falls back to iframe filename and then generated filename", () => {
  assert.equal(
    chooseShopCartFilename({ fileId: "abc", iframeFileName: "iframe.pdf" }),
    "iframe.pdf",
  );
  assert.equal(chooseShopCartFilename({ fileId: "abc" }), "资料车文件-abc");
});
