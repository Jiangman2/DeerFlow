# Production Shop Cart Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mock-only shop-cart attachment flow with a configurable production data lake iframe and download proxy while preserving mock fallback behavior.

**Architecture:** Keep iframe selection, payload parsing, download, and UI attachment orchestration in separate modules. The browser loads either the configured data lake iframe or the existing mock iframe, normalizes returned file selections, downloads real files through a Next API route, and adds resulting `File` objects through the existing `PromptInput` attachment API.

**Tech Stack:** Next.js App Router, React 19, TypeScript, `node:test`, existing `PromptInput` attachments, Next API route `fetch`, optional Node TLS dispatcher for internal data lake downloads.

---

## File Structure

- `frontend/src/core/shop-cart/selected-files.ts`: normalize iframe payloads into `{ fileId, fileName? }`.
- `frontend/src/core/shop-cart/selected-files.test.ts`: `node:test` coverage for supported and invalid payloads.
- `frontend/src/core/shop-cart/filenames.ts`: parse `Content-Disposition` and choose final filenames.
- `frontend/src/core/shop-cart/filenames.test.ts`: `node:test` coverage for filename rules.
- `frontend/src/core/shop-cart/download.ts`: browser helper that calls `/api/shop-cart/download` and converts responses to `File` results.
- `frontend/src/core/shop-cart/config.ts`: client-side shop-cart iframe URL/origin and mock-mode helpers.
- `frontend/src/app/api/shop-cart/download/route.ts`: server proxy to `https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download`.
- `frontend/src/env.js`: add public iframe config and server-only download token/TLS config.
- `frontend/src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx`: use configured iframe URL/origin and return normalized files.
- `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx`: use mock mapping in mock mode, real downloads in production mode.
- `frontend/src/core/i18n/locales/types.ts`, `zh-CN.ts`, `en-US.ts`: add production error/loading copy.
- `frontend/.env.example`: document public iframe config and server-side token/TLS variables.

### Task 1: Selected Payload Parser

**Files:**
- Create: `frontend/src/core/shop-cart/selected-files.test.ts`
- Create: `frontend/src/core/shop-cart/selected-files.ts`

- [ ] **Step 1: Write failing parser tests**

Create `frontend/src/core/shop-cart/selected-files.test.ts`:

```ts
import assert from "node:assert/strict";
import test from "node:test";

const { parseShopCartSelectedFiles } = await import(
  new URL("./selected-files.ts", import.meta.url).href
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
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --test frontend/src/core/shop-cart/selected-files.test.ts
```

Expected: fails with `Cannot find module ... selected-files.ts`.

- [ ] **Step 3: Implement parser**

Create `frontend/src/core/shop-cart/selected-files.ts`:

```ts
export type ShopCartSelectedFile = {
  fileId: string;
  fileName?: string;
};

export type ParseShopCartSelectedFilesResult =
  | { ok: true; files: ShopCartSelectedFile[] }
  | { ok: false; reason: "invalid_payload" };

function cleanString(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function parseShopCartSelectedFiles(
  payload: unknown,
): ParseShopCartSelectedFilesResult {
  if (!Array.isArray(payload)) {
    return { ok: false, reason: "invalid_payload" };
  }

  if (payload.every((item) => typeof item === "string")) {
    return {
      ok: true,
      files: payload
        .map((fileId) => cleanString(fileId))
        .filter((fileId): fileId is string => Boolean(fileId))
        .map((fileId) => ({ fileId })),
    };
  }

  const files: ShopCartSelectedFile[] = [];
  for (const item of payload) {
    if (!item || typeof item !== "object") {
      return { ok: false, reason: "invalid_payload" };
    }
    const record = item as Record<string, unknown>;
    const fileId = cleanString(record.fileId) ?? cleanString(record.id);
    if (!fileId) {
      return { ok: false, reason: "invalid_payload" };
    }
    const fileName = cleanString(record.fileName) ?? cleanString(record.name);
    files.push(fileName ? { fileId, fileName } : { fileId });
  }

  return { ok: true, files };
}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
node --test frontend/src/core/shop-cart/selected-files.test.ts
```

Expected: all tests pass.

### Task 2: Filename Parsing

**Files:**
- Create: `frontend/src/core/shop-cart/filenames.test.ts`
- Create: `frontend/src/core/shop-cart/filenames.ts`

- [ ] **Step 1: Write failing filename tests**

Create `frontend/src/core/shop-cart/filenames.test.ts`:

```ts
import assert from "node:assert/strict";
import test from "node:test";

const { chooseShopCartFilename, parseContentDispositionFilename } = await import(
  new URL("./filenames.ts", import.meta.url).href
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
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
node --test frontend/src/core/shop-cart/filenames.test.ts
```

Expected: fails with `Cannot find module ... filenames.ts`.

- [ ] **Step 3: Implement filename helpers**

Create `frontend/src/core/shop-cart/filenames.ts`:

```ts
export function parseContentDispositionFilename(
  contentDisposition: string | null | undefined,
): string | undefined {
  if (!contentDisposition) {
    return undefined;
  }

  const filenameStarMatch = /filename\*=([^;]+)/i.exec(contentDisposition);
  if (filenameStarMatch?.[1]) {
    const value = filenameStarMatch[1].trim().replace(/^"|"$/g, "");
    const encoded = value.includes("''") ? value.split("''").slice(1).join("''") : value;
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
    `资料车文件-${fileId}`
  );
}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
node --test frontend/src/core/shop-cart/filenames.test.ts
```

Expected: all tests pass.

### Task 3: Config And Download Helpers

**Files:**
- Create: `frontend/src/core/shop-cart/config.ts`
- Create: `frontend/src/core/shop-cart/download.ts`
- Modify: `frontend/src/env.js`
- Modify: `frontend/.env.example`

- [ ] **Step 1: Add env schema**

Modify `frontend/src/env.js`:

```ts
server: {
  // existing keys...
  SHOP_CART_DOWNLOAD_TOKEN: z.string().optional(),
  SHOP_CART_DOWNLOAD_TLS_VERIFY: z.string().optional(),
},
client: {
  // existing keys...
  NEXT_PUBLIC_SHOP_CART_IFRAME_URL: z.string().optional(),
  NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN: z.string().optional(),
},
runtimeEnv: {
  // existing keys...
  SHOP_CART_DOWNLOAD_TOKEN: process.env.SHOP_CART_DOWNLOAD_TOKEN,
  SHOP_CART_DOWNLOAD_TLS_VERIFY: process.env.SHOP_CART_DOWNLOAD_TLS_VERIFY,
  NEXT_PUBLIC_SHOP_CART_IFRAME_URL: process.env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL,
  NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN:
    process.env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN,
},
```

Keep all existing env keys intact.

- [ ] **Step 2: Add env examples**

Append to `frontend/.env.example`:

```env
# Shop cart integration
# NEXT_PUBLIC_SHOP_CART_IFRAME_URL="https://datalake.cnooc/resourceApplicationQuery/businessResourceSearch/menuSearch"
# NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN="https://datalake.cnooc"
# SHOP_CART_DOWNLOAD_TOKEN="your-platform-token"
# SHOP_CART_DOWNLOAD_TLS_VERIFY="false"
```

- [ ] **Step 3: Create client config helper**

Create `frontend/src/core/shop-cart/config.ts`:

```ts
import { env } from "@/env";

export const SHOP_CART_MOCK_IFRAME_URL = "/mock/shop-cart-iframe";

export function getShopCartFrameConfig(currentOrigin: string) {
  const iframeUrl = env.NEXT_PUBLIC_SHOP_CART_IFRAME_URL?.trim();
  const allowedOrigin = env.NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN?.trim();

  return {
    iframeUrl: iframeUrl || SHOP_CART_MOCK_IFRAME_URL,
    allowedOrigin: allowedOrigin || currentOrigin,
    isMockMode: !iframeUrl,
  };
}
```

- [ ] **Step 4: Create download helper**

Create `frontend/src/core/shop-cart/download.ts`:

```ts
import { chooseShopCartFilename } from "./filenames";
import type { ShopCartSelectedFile } from "./selected-files";

export type DownloadShopCartFileResult =
  | { ok: true; file: File }
  | { ok: false; fileId: string; status?: number; message: string };

export async function downloadShopCartFile(
  selectedFile: ShopCartSelectedFile,
): Promise<DownloadShopCartFileResult> {
  try {
    const response = await fetch(
      `/api/shop-cart/download?fileId=${encodeURIComponent(selectedFile.fileId)}`,
    );
    if (!response.ok) {
      return {
        ok: false,
        fileId: selectedFile.fileId,
        status: response.status,
        message: `Download failed with status ${response.status}`,
      };
    }

    const blob = await response.blob();
    const filename = chooseShopCartFilename({
      contentDisposition: response.headers.get("content-disposition"),
      fileId: selectedFile.fileId,
      iframeFileName: selectedFile.fileName,
    });

    return {
      ok: true,
      file: new File([blob], filename, {
        type: response.headers.get("content-type") ?? blob.type,
      }),
    };
  } catch (error) {
    return {
      ok: false,
      fileId: selectedFile.fileId,
      message: error instanceof Error ? error.message : "Download failed",
    };
  }
}
```

### Task 4: Next API Download Proxy

**Files:**
- Create: `frontend/src/app/api/shop-cart/download/route.ts`

- [ ] **Step 1: Create proxy route**

Create `frontend/src/app/api/shop-cart/download/route.ts`:

```ts
import { Agent } from "node:https";

import { NextResponse, type NextRequest } from "next/server";

import { env } from "@/env";

const DATA_LAKE_DOWNLOAD_BASE =
  "https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download";

function buildDownloadHeaders() {
  const headers = new Headers();
  const token = env.SHOP_CART_DOWNLOAD_TOKEN?.trim();
  if (token) {
    headers.set("authorization", `Bearer ${token}`);
  }
  return headers;
}

function buildFetchInit(): RequestInit {
  const init: RequestInit = {
    headers: buildDownloadHeaders(),
  };

  if (env.SHOP_CART_DOWNLOAD_TLS_VERIFY === "false") {
    Object.assign(init, {
      dispatcher: new Agent({ rejectUnauthorized: false }),
    });
  }

  return init;
}

export async function GET(request: NextRequest) {
  const fileId = request.nextUrl.searchParams.get("fileId")?.trim();
  if (!fileId) {
    return NextResponse.json({ detail: "fileId is required" }, { status: 400 });
  }

  const url = new URL(DATA_LAKE_DOWNLOAD_BASE);
  url.searchParams.set("fileId", fileId);

  let response: Response;
  try {
    response = await fetch(url, buildFetchInit());
  } catch {
    return NextResponse.json(
      { detail: "Failed to reach data lake download service" },
      { status: 502 },
    );
  }

  if (!response.ok) {
    return NextResponse.json(
      { detail: "Data lake download failed" },
      { status: response.status },
    );
  }

  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  const contentDisposition = response.headers.get("content-disposition");
  if (contentType) headers.set("content-type", contentType);
  if (contentDisposition) headers.set("content-disposition", contentDisposition);

  return new Response(response.body, {
    status: response.status,
    headers,
  });
}
```

- [ ] **Step 2: Run typecheck and fix Node fetch dispatcher typing if needed**

Run:

```bash
cd frontend && pnpm typecheck
```

Expected: pass. If TypeScript rejects `dispatcher`, replace the `Object.assign` block with a local extended type:

```ts
type NodeRequestInit = RequestInit & {
  dispatcher?: Agent;
};
const init: NodeRequestInit = { headers: buildDownloadHeaders() };
```

Then return `init`.

### Task 5: Wire Dialog And Button

**Files:**
- Modify: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx`
- Modify: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx`
- Modify: `frontend/src/core/i18n/locales/types.ts`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`

- [ ] **Step 1: Update i18n types and copy**

Add these keys to `inputBox` in `frontend/src/core/i18n/locales/types.ts`:

```ts
shopCartInvalidPayload: string;
shopCartDownloading: string;
shopCartDownloadFailed: string;
shopCartPartialDownloadFailed: (failed: number) => string;
```

Add Chinese strings in `zh-CN.ts`:

```ts
shopCartInvalidPayload: "资料车返回的数据格式无法识别",
shopCartDownloading: "正在下载资料车文件...",
shopCartDownloadFailed: "资料车文件下载失败，请重试",
shopCartPartialDownloadFailed: (failed: number) =>
  `部分资料车文件下载失败，失败 ${failed} 个`,
```

Add English strings in `en-US.ts`:

```ts
shopCartInvalidPayload: "The shop cart returned an unsupported data format.",
shopCartDownloading: "Downloading shop cart files...",
shopCartDownloadFailed: "Failed to download shop cart files. Please retry.",
shopCartPartialDownloadFailed: (failed: number) =>
  `${failed} shop cart file${failed === 1 ? "" : "s"} failed to download.`,
```

- [ ] **Step 2: Update dialog props and origin handling**

In `shop-cart-dialog.tsx`:

- Import `getShopCartFrameConfig` and `parseShopCartSelectedFiles`.
- Change `onSelectedIds` prop to `onSelectedFiles: (files: ShopCartSelectedFile[], isMockMode: boolean) => boolean | void`.
- Derive frame config only in browser-safe code after open render using `window.location.origin`.
- Set iframe `src` to `frameConfig.iframeUrl`.
- Send `postMessage` to `frameConfig.allowedOrigin`.
- Receive only `event.origin === frameConfig.allowedOrigin`.
- Parse `event.data.data` with `parseShopCartSelectedFiles`.
- On invalid payload, toast `t.inputBox.shopCartInvalidPayload` and keep dialog open.

- [ ] **Step 3: Update attachment button behavior**

In `shop-cart-attachment-button.tsx`:

- Replace `handleSelectedIds(ids: string[])` with `handleSelectedFiles(selectedFiles: ShopCartSelectedFile[], isMockMode: boolean)`.
- If empty, keep current empty-selection behavior.
- If `isMockMode`, call `createMockShopCartFiles(selectedFiles.map((file) => file.fileId))`.
- If production mode, toast loading with `toast.loading(t.inputBox.shopCartDownloading)`, call `downloadShopCartFile` for each selected file, dismiss loading toast, add successful files, and report partial or total failure.

Use this production result logic:

```ts
const results = await Promise.all(selectedFiles.map(downloadShopCartFile));
const files = results
  .filter((result): result is { ok: true; file: File } => result.ok)
  .map((result) => result.file);
const failed = results.length - files.length;

if (files.length === 0) {
  toast.error(t.inputBox.shopCartDownloadFailed);
  return false;
}

attachments.add(files);
toast.success(t.inputBox.shopCartAddedFiles(files.length));
if (failed > 0) {
  toast.warning(t.inputBox.shopCartPartialDownloadFailed(failed));
}
return true;
```

### Task 6: Verification

**Files:**
- All files changed in Tasks 1-5.

- [ ] **Step 1: Run shop-cart unit tests**

Run:

```bash
node --test frontend/src/core/shop-cart/mock-files.test.ts frontend/src/core/shop-cart/selected-files.test.ts frontend/src/core/shop-cart/filenames.test.ts
```

Expected: all tests pass.

- [ ] **Step 2: Run TypeScript**

Run:

```bash
cd frontend && pnpm typecheck
```

Expected: pass.

- [ ] **Step 3: Run full lint**

Run:

```bash
cd frontend && pnpm lint
```

Expected: pass, or fail only on unrelated pre-existing files. If it fails, capture the exact file/line failures.

- [ ] **Step 4: Run changed-file lint**

Run:

```bash
cd frontend && pnpm exec eslint src/app/api/shop-cart/download/route.ts src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx src/core/shop-cart/config.ts src/core/shop-cart/download.ts src/core/shop-cart/filenames.ts src/core/shop-cart/filenames.test.ts src/core/shop-cart/selected-files.ts src/core/shop-cart/selected-files.test.ts src/core/i18n/locales/types.ts src/core/i18n/locales/zh-CN.ts src/core/i18n/locales/en-US.ts src/env.js --ext .ts,.tsx,.js
```

Expected: pass.

- [ ] **Step 5: Manual smoke**

Run:

```bash
cd frontend && pnpm dev
```

Open `http://localhost:3000/workspace/chats/new`.

Expected without shop-cart env vars:

- The existing mock iframe opens.
- Clicking `添加所选文件` adds mock files to the attachment list.

Expected with shop-cart env vars set:

- The iframe URL is `https://datalake.cnooc/resourceApplicationQuery/businessResourceSearch/menuSearch`.
- Clicking `添加所选文件` sends `getShopCartSelectedIds`.
- Downloads call `/api/shop-cart/download?fileId=...`.
