# Shop Cart Attachments Demo Design

## Goal

Build a demo that lets users add files from a mock "资料车" iframe into the existing chat composer as normal attachments. The demo should validate the end-to-end interaction before integrating the real CNOOC data lake cart page and download API.

## Scope

In scope:

- Add a "资料车" button next to the existing local attachment button in the chat input toolbar.
- Open a dialog that embeds a local mock iframe.
- Let users select mock shop cart files inside the iframe.
- Use `postMessage` between the parent chat page and the iframe.
- Convert selected mock file IDs into local mock `File` objects.
- Add those files to the existing `PromptInput` attachment state so the current upload, preview, remove, and send flow is reused.
- Support the normal chat page first. Agent chat pages can reuse the same `InputBox` integration if no route-specific dependency blocks it.

Out of scope for this demo:

- Calling `https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download?fileId=...`.
- Authenticating against the real data lake.
- Persisting shop cart selections outside the composer.
- Deduplicating selected files.
- Building the final production backend proxy for data lake downloads.

## Existing Context

The frontend is a Next.js chat app. The chat composer is implemented in `frontend/src/components/workspace/input-box.tsx`. Existing local file attachments are managed by `usePromptInputAttachments()` from `frontend/src/components/ai-elements/prompt-input.tsx`.

The submission flow in `frontend/src/core/threads/hooks.ts` already converts composer file parts into `File` objects, uploads them through `uploadFiles()`, and sends uploaded file metadata in `additional_kwargs.files`. The demo should reuse this path instead of creating a separate attachment model.

## User Flow

1. The user opens a chat page.
2. The chat input toolbar shows the existing paperclip button and a new "资料车" button.
3. The user clicks "资料车".
4. A dialog opens with a local mock iframe.
5. The iframe shows mock file rows with checkboxes and an iframe-side confirm action.
6. The user selects one or more files and confirms.
7. The parent page receives selected file IDs through `postMessage`.
8. The parent maps each known ID to a mock file definition and creates local `File` objects.
9. The parent calls `attachments.add(files)`.
10. The selected files appear in the existing composer attachment list.
11. The user can remove the attachments or send the message.

## Components

### `ShopCartAttachmentButton`

Responsibilities:

- Render the "资料车" toolbar button in `InputBox`.
- Open and close the shop cart dialog.
- Access the existing composer attachment controller through `usePromptInputAttachments()`.
- Receive selected file IDs from the dialog and add mapped files to the existing attachment state.

Dependencies:

- `PromptInputButton`
- `Tooltip`
- `usePromptInputAttachments`
- `ShopCartDialog`

### `ShopCartDialog`

Responsibilities:

- Render a `Dialog` that contains an iframe.
- Host the parent-side `postMessage` lifecycle.
- Request selected IDs from the iframe using `{ type: "getShopCartSelectedIds" }`.
- Listen for `{ type: "shopCartSelectedIds", data: string[] }`.
- Return the selected IDs to `ShopCartAttachmentButton`.
- Show useful toast messages for empty selections, unknown IDs, and iframe failures.

Demo behavior:

- The primary demo flow uses the iframe-side confirm button. When clicked, the iframe posts `{ type: "shopCartSelectedIds", data: selectedIds }` to the parent.
- The iframe should also listen for parent requests with `{ type: "getShopCartSelectedIds" }` and return the same selected ID payload. This keeps the mock compatible with the supplied integration snippet and the future real iframe contract.
- The iframe route is same-origin in the demo, so origin checks can be strict to `window.location.origin`.

### Mock iframe route

Suggested route:

- `frontend/src/app/mock/shop-cart-iframe/page.tsx`

Responsibilities:

- Render a simple mock file list with checkboxes.
- Maintain selected IDs locally.
- Listen for parent messages with `type === "getShopCartSelectedIds"`.
- Reply to `window.parent` with `{ type: "shopCartSelectedIds", data: selectedIds }`.
- Include an iframe-side confirm button that sends the same message.

## Mock File Mapping

The demo should use a small static mapping, for example:

| File ID | Filename | MIME type | Mock content |
| --- | --- | --- | --- |
| `file-001` | `生产日报.pdf` | `application/pdf` | Text blob that identifies the mock PDF |
| `file-002` | `勘探数据.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | Text or CSV-like blob for demo upload |
| `file-003` | `会议纪要.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Text blob that identifies the mock document |

The file content does not need to be a valid Office or PDF binary for the demo, because the goal is to validate composer attachment behavior. The filename and MIME type should reflect the intended file.

## Error Handling

- Empty selection: show `请选择至少一个资料车文件` and do not add attachments.
- iframe timeout or no response: show `未获取到资料车文件，请重试`; keep the dialog open.
- Unknown returned IDs: skip unknown IDs and show a warning that some files could not be recognized.
- All returned IDs unknown: show an error and add nothing.
- Duplicate selection: allow duplicates in the demo to match the existing local attachment behavior.

## Security Notes

Demo:

- Use a same-origin mock iframe route.
- Use strict origin validation against `window.location.origin` where practical.

Production integration:

- Replace wildcard `targetOrigin` with the real data lake origin.
- Validate `event.origin` before reading `event.data`.
- Avoid downloading data lake files directly from the browser if CORS, cookies, internal certificates, or auth headers are involved.
- Prefer a backend or Next API proxy for the real download URL:
  `https://datalake.cnooc/jsearch/bd_search/external-download/api/cart/download?fileId=<fileId>`.

## Testing And Acceptance

Manual acceptance:

- The chat input shows a "资料车" button.
- Clicking it opens a dialog with the mock iframe.
- Selecting files in the iframe and clicking its confirm action returns file IDs to the parent page.
- Selected files appear in the existing composer attachment list.
- The user can remove selected files.
- Sending a message with those attachments uses the existing upload flow.
- Empty selection, iframe failure, and unknown IDs produce understandable feedback.

Verification commands:

- `pnpm lint`
- `pnpm typecheck`

Manual browser verification:

- Start the frontend with `pnpm dev`.
- Open a normal chat route.
- Run the full selection and send flow.

## Implementation Boundaries

Keep the demo changes focused on the frontend composer and local mock route. Do not change backend upload contracts or thread submission contracts unless the existing attachment flow cannot accept generated `File` objects. If that happens, the implementation should fix the smallest necessary boundary in the composer attachment API instead of adding a second upload path.
