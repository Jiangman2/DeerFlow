# Production Shop Cart Integration Design

## Goal

Upgrade the current mock shop-cart attachment demo into a production integration with the real data lake shop cart iframe at `https://datalake.cnooc/resourceApplicationQuery/businessResourceSearch/menuSearch`, while preserving the local mock fallback for development and demos.

## Current Context

The current frontend has a mock-only implementation:

- `frontend/src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx` opens an iframe hard-coded to `/mock/shop-cart-iframe`.
- `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx` converts selected IDs into local mock `File` objects with `createMockShopCartFiles()`.
- `frontend/src/core/shop-cart/mock-files.ts` contains static mock file definitions.
- `frontend/src/app/mock/shop-cart-iframe/page.tsx` simulates the iframe side of the `postMessage` contract.

The production implementation should reuse the existing composer attachment path. Files must still be added through `usePromptInputAttachments().add(files)` so remove, preview, send, and upload behavior stays consistent with normal local attachments.

## Scope

In scope:

- Add configuration for the production shop cart iframe URL and allowed origin.
- Preserve mock mode when the production iframe URL is not configured.
- Keep the parent-side request protocol: `{ type: "getShopCartSelectedIds" }`.
- Keep the response protocol: `{ type: "shopCartSelectedIds", data: ... }`.
- Parse both `string[]` file ID payloads and object-array payloads.
- Download real files through a Next API proxy.
- Convert downloaded responses into `File` objects and add them to the existing attachment list.
- Add unit tests for pure parsing and filename logic.

Out of scope:

- Hard-coding data lake tokens or user credentials in source code.
- Changing backend upload contracts.
- Deduplicating selected files.
- Persisting shop-cart selections outside the current composer.
- Modifying the real data lake iframe page.

## Configuration

Add client-visible environment variables:

```env
NEXT_PUBLIC_SHOP_CART_IFRAME_URL=https://datalake.cnooc/resourceApplicationQuery/businessResourceSearch/menuSearch
NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN=https://datalake.cnooc
```

Runtime behavior:

- If `NEXT_PUBLIC_SHOP_CART_IFRAME_URL` is set, use it as the iframe `src`.
- If it is not set, use `/mock/shop-cart-iframe`.
- If `NEXT_PUBLIC_SHOP_CART_ALLOWED_ORIGIN` is set, use it as `postMessage` target origin and receive origin.
- If it is not set, use `window.location.origin` for mock mode.

Server-side download target:

```text
https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download?fileId=<fileId>
```

For this implementation, hard-code the server-side data lake download URL to the confirmed host and path above. Do not add a server-side environment variable for the download URL in this pass.

Add server-only environment variables for data lake download behavior:

```env
SHOP_CART_DOWNLOAD_TOKEN=<platform token>
SHOP_CART_DOWNLOAD_TLS_VERIFY=false
```

Runtime behavior:

- If `SHOP_CART_DOWNLOAD_TOKEN` is set, the proxy sends `Authorization: Bearer <token>` to the data lake download URL.
- If it is not set, the proxy sends no authorization header.
- `SHOP_CART_DOWNLOAD_TLS_VERIFY=false` allows the internal data lake HTTPS request to skip TLS certificate verification, matching the provided Python reference that uses `verify=False`.
- The default TLS behavior remains verification enabled unless this variable is explicitly set to `false`.

## iframe Communication

Parent-to-iframe request:

```ts
iframe.contentWindow.postMessage(
  { type: "getShopCartSelectedIds" },
  targetOrigin,
);
```

Accepted iframe response:

```ts
{ type: "shopCartSelectedIds", data: payload }
```

Supported payload shapes:

```ts
["fileId1", "fileId2"]
```

```ts
[
  { fileId: "fileId1", fileName: "日报.pdf" },
  { id: "fileId2", name: "勘探数据.xlsx" }
]
```

Normalize into:

```ts
type ShopCartSelectedFile = {
  fileId: string;
  fileName?: string;
};
```

Security checks:

- Send messages with the configured `targetOrigin`.
- Accept responses only when `event.origin === allowedOrigin`.
- Accept responses only when `event.source === iframe.contentWindow`.
- Ignore unknown message types.
- Treat unrecognized payload shapes as a user-visible error.

## Download Flow

For each normalized selected file:

1. Frontend requests:

   ```text
   /api/shop-cart/download?fileId=<fileId>
   ```

2. Next API route requests:

   ```text
   https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download?fileId=<fileId>
   ```

3. The proxy returns the data lake response body and forwards useful response headers:
   - `Content-Type`
   - `Content-Disposition`

4. Frontend creates a `File` from the response:
   - Prefer `Content-Disposition` `filename*`.
   - Then `Content-Disposition` `filename`.
   - Then iframe payload `fileName`.
   - Then fallback to `资料车文件-${fileId}`.

5. Frontend calls:

   ```ts
   attachments.add(files);
   ```

Authentication:

- The proxy supports an optional Bearer token through `SHOP_CART_DOWNLOAD_TOKEN`.
- The token must never be committed to the repository.
- If the real data lake still returns `401` or `403`, the same proxy route is the extension point for Cookie forwarding or a different service credential scheme.

## Components And Modules

### `frontend/src/core/shop-cart/selected-files.ts`

Responsibilities:

- Define `ShopCartSelectedFile`.
- Parse iframe payloads into normalized selected files.
- Return invalid payload errors without throwing from UI code.

### `frontend/src/core/shop-cart/filenames.ts`

Responsibilities:

- Parse `Content-Disposition` filename and `filename*`.
- Choose final filename with the priority:
  response header, iframe filename, fallback.

### `frontend/src/core/shop-cart/download.ts`

Responsibilities:

- Fetch `/api/shop-cart/download?fileId=...`.
- Convert successful responses into `File` objects.
- Return enough status detail for partial-success UI feedback.

### `frontend/src/app/api/shop-cart/download/route.ts`

Responsibilities:

- Validate `fileId`.
- Request the data lake download URL.
- Add `Authorization: Bearer <token>` when `SHOP_CART_DOWNLOAD_TOKEN` is configured.
- Use normal TLS verification by default and disable it only when `SHOP_CART_DOWNLOAD_TLS_VERIFY=false`.
- Forward successful response body, content type, and content disposition.
- Return `400` when `fileId` is missing.
- Return the data lake status with a concise error message for non-2xx responses.
- Return `502` for network errors.

### Existing UI Components

Update `ShopCartDialog`:

- Use configured iframe URL and allowed origin.
- Keep mock fallback.
- Return normalized selected file records, not raw IDs.

Update `ShopCartAttachmentButton`:

- In mock mode, keep using `createMockShopCartFiles()` for local demo behavior.
- In production mode, call the real download helper for each selected file.
- Add successfully downloaded `File` objects to existing attachments.
- Report partial and total failures with toast messages.

## Error Handling

UI:

- Empty selection: `请选择至少一个资料车文件`.
- iframe timeout or no response: `未获取到资料车文件，请重试`.
- Unrecognized iframe payload: `资料车返回的数据格式无法识别`.
- Some downloads fail: add successful files and show `部分资料车文件下载失败`.
- All downloads fail: add nothing and show `资料车文件下载失败，请重试`.
- Duplicate selections remain allowed.

API:

- Missing `fileId`: `400`.
- Data lake non-2xx response: same status code, concise JSON error.
- Network or fetch failure: `502`.

## Testing And Acceptance

Unit tests:

- Parse `string[]` payloads.
- Parse object-array payloads with `fileId` / `fileName`.
- Parse object-array payloads with `id` / `name`.
- Reject unrecognized payloads.
- Parse `Content-Disposition` `filename`.
- Parse `Content-Disposition` `filename*`.
- Fall back to iframe filename and then `资料车文件-${fileId}`.

Verification:

- `node --test` for new shop-cart pure function tests.
- `pnpm typecheck`.
- changed-file ESLint.
- Run full `pnpm lint` and report its result. If it fails only on pre-existing unrelated files, also run changed-file ESLint for the files touched by this implementation and report both results.

Manual acceptance:

- With no shop-cart environment variables, the mock iframe still works.
- With production variables set, the dialog loads `https://datalake.cnooc/resourceApplicationQuery/businessResourceSearch/menuSearch`.
- With `SHOP_CART_DOWNLOAD_TOKEN` set, the proxy sends the same Bearer-style authorization shown in the Python reference.
- Clicking `添加所选文件` sends `getShopCartSelectedIds` to the iframe.
- Both supported payload shapes produce normalized file records.
- Each selected file calls `/api/shop-cart/download?fileId=...`.
- Downloaded files appear in the existing composer attachment list.
- Partial download failures produce partial-success feedback.
