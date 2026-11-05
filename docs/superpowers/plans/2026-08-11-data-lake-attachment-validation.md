# Data Lake Attachment Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Data Lake files that violate the chat upload limits from showing a false success message before they are removed.

**Architecture:** Keep attachment admission in `InputBox`, which already owns the current attachment list, Gateway limits, and localized violation reporting. Make the Data Lake button responsible only for listing and downloading files; it passes downloaded `File` objects to a small callback and reports success using the callback's accepted count. Existing state-effect, submit-time, and Gateway validation remain as defense in depth.

**Tech Stack:** React 19, TypeScript, Rstest, Sonner

---

### Task 1: Lock Down Data Lake Success Reporting

**Files:**

- Modify: `frontend/tests/unit/components/workspace/shop-cart-attachment-button.test.ts`
- Modify: `frontend/src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx`

- [x] **Step 1: Write the failing component test**

Capture `NewShopCartDialog.onSelectedFiles`, mock a successful Data Lake download, and pass an admission callback that accepts zero files. Assert that the callback receives the downloaded file, the success toast is not shown, and the dialog remains open.

- [x] **Step 2: Run the test to verify it fails**

Run:

```bash
pnpm test run tests/unit/components/workspace/shop-cart-attachment-button.test.ts
```

Expected: FAIL because the current component calls `attachments.add(files)` directly and always reports the number downloaded.

- [x] **Step 3: Implement the minimal callback interface**

Add the required prop:

```ts
onAddFiles: (files: File[]) => number;
```

After downloads complete, call `onAddFiles(files)`, show success only when the returned count is positive, and return `false` when every downloaded file was rejected.

- [x] **Step 4: Run the focused test to verify it passes**

Run the same Rstest command and expect all tests in the file to pass.

### Task 2: Admit Data Lake Files Through InputBox Validation

**Files:**

- Modify: `frontend/src/components/workspace/input-box.tsx`
- Test: `frontend/tests/unit/core/uploads/file-validation.test.ts`

- [x] **Step 1: Extend the validation regression coverage**

Add a 60 MiB ZIP case using the existing 50 MiB limit and assert that the shared `validateIncomingUploadFiles` admission helper rejects it with `max_file_size`.

- [x] **Step 2: Verify the new regression test fails before the admission helper exists, then passes after implementation**

Run:

```bash
pnpm test run tests/unit/core/uploads/file-validation.test.ts
```

Expected RED: `validateIncomingUploadFiles is not a function`. Expected GREEN after implementation: PASS.

- [x] **Step 3: Add the InputBox admission callback**

Use the shared admission helper so production code and regression tests exercise the same package and limit composition:

```ts
const addValidatedAttachments = useCallback(
  (incomingFiles: File[]) => {
    const existingFiles = attachmentParts.flatMap((attachment) =>
      attachment.file instanceof File ? [attachment.file] : [],
    );
    const validation = validateIncomingUploadFiles(
      existingFiles,
      incomingFiles,
      uploadLimits,
    );
    if (validation.unsupportedMessage) {
      toast.error(validation.unsupportedMessage);
    }
    reportUploadLimitViolations(validation.violations);
    if (validation.accepted.length > 0) {
      addAttachmentFiles(validation.accepted);
    }
    return validation.accepted.length;
  },
  [
    addAttachmentFiles,
    attachmentParts,
    reportUploadLimitViolations,
    uploadLimits,
  ],
);
```

Pass this callback to `ShopCartAttachmentButton`. Keep the existing whole-state effect and submit-time check unchanged.

- [x] **Step 4: Run focused tests and type checking**

```bash
pnpm test run tests/unit/components/workspace/shop-cart-attachment-button.test.ts tests/unit/core/uploads/file-validation.test.ts
pnpm typecheck
```

Expected: both commands exit successfully.

### Task 3: Verify the User-Facing Flow

**Files:**

- Modify only if a test exposes a defect: files from Tasks 1-2

- [x] **Step 1: Run related unit tests**

```bash
pnpm test run tests/unit/components/workspace/shop-cart-attachment-button.test.ts tests/unit/core/shop-cart/api-files.test.ts tests/unit/core/uploads/file-validation.test.ts
```

- [x] **Step 2: Run lint on touched source and test files**

```bash
pnpm exec eslint src/components/workspace/input-box.tsx src/components/workspace/shop-cart-attachments/shop-cart-attachment-button.tsx tests/unit/components/workspace/shop-cart-attachment-button.test.ts tests/unit/core/uploads/file-validation.test.ts
```

- [ ] **Step 3: Attempt the rendered Playwright regression**

Use the repository Playwright flow when Chromium is available. If the environment still lacks the configured browser executable, report that limitation without claiming browser verification.

- [x] **Step 4: Review the final diff**

Confirm the implementation changes only Data Lake attachment admission and tests, retains all backend enforcement, and does not alter unrelated user files.
