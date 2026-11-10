# Attachment sync, bounded conversion and Work production configuration

Candidate parent: `15e561050755a31c4e69d682df11321a4ab4f7c5`.
Parent image: `sha256:d13c5343702c28316bee686292ab2ff76edb0151baf17af3f198ce203df992ee`.

Revision r4 corrects the failure fixture without changing r3 runtime files or
Work configuration. Server r3 QA completed in 55.01 seconds with 302 passed,
5 skipped and one failed assertion: the normal XLSX conversion passed, but the
replacement bytes `not an xlsx` were accepted by content detection as plain text.
The failure case now removes the input to exercise a deterministic real-worker
error. Two additional cases use a real child that writes private partial output
then exits with code 7; they require temporary cleanup, capacity release, no new
published Markdown, and preservation of an existing complete Markdown file.
These new Linux cases and the complete suite must pass before source acceptance.

Revision r3 adds explicit ONNX Runtime session options inside the conversion
child only. The r2 Linux probe of a normal two-row XLSX stopped in Magika's
ONNX session initialization: CPU time remained at 7.2 seconds while virtual
address space approached the 1 GiB cap and the main thread waited on a futex.
Magika 0.6.3 creates a default CPU session; BLAS/OMP thread variables do not
configure its pool. R3 sets intra/inter-op threads to one, sequential execution
and no spinning, restoring the constructor after conversion or failure. The
model, providers, converter and all resource caps remain unchanged. Linux
testing must establish whether this resolves the observed initialization hang;
the timeout test alone is not evidence that normal conversion works.

## Problem and evidence

The Feishu channel (also used by Haineng Work) saved an attachment in Gateway,
then synchronously acquired a Sandbox and wrote the same file through its API.
The browser upload path already distinguishes shared thread-data mounts. On a
shared mount, the second writer can have a different UID and lack permission to
overwrite a Gateway-owned file. Both acquisition and remote write also blocked
the async message handler's event loop.

The saved November 10 log reports PermissionError inside Sandbox file write and
a following lease loss on p57cz. That Pod had not restarted. The exact failed
path, current mount configuration and ownership still require the supplied
read-only production diagnostic. The 40-second health-log gap alone is not an
incident-time stack and does not establish the duration of event-loop blocking.

The morning 24,883-byte XLSX matches an earlier isolated reproduction with a
real cell near row 1,048,570. The former size threshold ran small conversions
inline; larger conversions ran in a thread sharing Gateway memory. The separate
afternoon gw4l5 OOM cannot yet be attributed to either of its large uploads.

## Candidate behavior

* Channel attachments are atomically saved in the explicit user's thread
  bucket, readable with mode 0644. Shared-mount providers skip the duplicate
  Sandbox write. Non-shared providers still synchronize and report failure
  honestly. Storage, acquisition and remote sync run off the event loop, with
  at most two active workers and four waiting callers per Gateway process.
* All local conversions use a Linux child process. Per Gateway process, only
  one conversion runs, with at most two waiting callers and a two-second
  admission timeout. Child limits: 120 seconds wall time, 90 CPU seconds,
  1 GiB virtual address space, 16 MiB Markdown output. Child process-group
  cleanup completes before a cancelled caller releases its capacity slot.
* Existing converter selection, XLSX compatibility, atomic Markdown publication
  and caller failure contract are preserved. No sheet dimensions or populated
  cells are silently discarded. An excessive document fails parsing; this does
  not promise every pathological workbook can be converted successfully.
* Enterprise PDF/Word/PPT parsing and the two production upload code mounts are
  unchanged. Linux resource limits are required; other platforms fail local
  conversion explicitly. Limits are per child/process, not a whole-Pod memory
  guarantee or isolation of every other Gateway feature.

## Work settings and release

`deploy/enterprise/work-production-env-20261110.json` contains the nine deferred
non-secret settings. Apply them with the accepted image in one Deployment
template update, preserving existing enabled/profile values, state key,
credentials, uploads mounts and scheduling. Configuring scopes does not grant
upstream application/user authorization. Existing scheduled tasks targeting
Work may begin sending notifications once enabled; their normal binding and
authorization checks remain required.

The source-QA bundle extracts the exact parent into a new record directory,
runs historical verifiers on that untouched parent, then validates the finite
candidate delta and runs combined tests. It does not rewrite old sealed
manifests or claim historical verifiers accept the new runtime bytes. A future
source PR must carry this delta's provenance into the historical projection
chain before treating the entire repository as release-verified.

Source QA is not installed-image QA. Next gates are installed imports and
tests (including Linux limit tests), private Gateway startup, current mounts
and Work credential readiness, node preload, rollout and business acceptance.
Use a test Work attachment and a normal spreadsheet, then the known troublesome
XLSX in isolation while observing health and lease renewal. Never reproduce an
unbounded parser on a live Gateway. No production changes are made by this
source-QA bundle or its diagnostic.
