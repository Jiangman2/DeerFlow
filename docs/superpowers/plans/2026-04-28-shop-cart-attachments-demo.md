# Shop Cart Attachments Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a frontend demo that lets users select mock shop-cart files from an iframe and attach them to the existing chat composer.

**Architecture:** Keep the attachment path inside the existing `PromptInput` state. Add a focused mock-file utility, a dialog/button component pair, and a same-origin mock iframe route that communicates through `postMessage`.

**Tech Stack:** Next.js App Router, React 19, TypeScript, Shadcn/Radix Dialog, existing `PromptInput` attachment API, `node:test` for the mock-file utility.

---

### Task 1: Mock File Utility

**Files:**
- Create: `frontend/src/core/shop-cart/mock-files.ts`
- Create: `frontend/src/core/shop-cart/mock-files.test.ts`

- [ ] Write a `node:test` test that imports `createMockShopCartFiles()` and verifies known IDs become `File` objects while unknown IDs are returned separately.
- [ ] Run `node --test frontend/src/core/shop-cart/mock-files.test.ts` and confirm it fails because the module does not exist.
- [ ] Implement `SHOP_CART_MOCK_FILES`, `createMockShopCartFiles(ids)`, and the returned `{ files, unknownIds }` shape.
- [ ] Run the test again and confirm it passes.

### Task 2: Mock iframe Route

**Files:**
- Create: `frontend/src/app/mock/shop-cart-iframe/page.tsx`

- [ ] Build a client page with three mock rows and checkboxes for the same IDs defined by the utility.
- [ ] Add an iframe-side confirm button that posts `{ type: "shopCartSelectedIds", data: selectedIds }` to `window.parent`.
- [ ] Add a message listener for `{ type: "getShopCartSelectedIds" }` that sends the same payload.

### Task 3: Composer Dialog And Button

**Files:**
- Create: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-dialog.tsx`
- Create: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx`
- Create: `frontend/src/components/workspace/shop-cart-attachments/index.ts`
- Modify: `frontend/src/components/workspace/input-box.tsx`

- [ ] Add `ShopCartDialog` with an iframe, strict same-origin event handling, empty-selection validation, timeout handling for parent-request flow, and close-on-success behavior.
- [ ] Add `ShopCartAttachmentButton` that calls `createMockShopCartFiles()` and `usePromptInputAttachments().add(files)`.
- [ ] Render `ShopCartAttachmentButton` next to `AddAttachmentsButton` in `InputBox`.

### Task 4: Copy And Verification

**Files:**
- Modify: `frontend/src/core/i18n/locales/types.ts`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`

- [ ] Add localized strings for the button tooltip, dialog title, description, parent-side add action, and feedback messages.
- [ ] Run `pnpm lint` from `frontend/`.
- [ ] Run `pnpm typecheck` from `frontend/`.
- [ ] Start `pnpm dev` from `frontend/` and manually verify the chat composer flow.
