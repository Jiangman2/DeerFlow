/** Task-local intent, never a saved preference or authority for old runs. */
export function isPptConfirmationRequest(text: string): boolean {
  return /^\/ppt-master(?:\s|$)/i.test(text.trimStart());
}

export function confirmationSubmission({
  text,
  threadId,
  optedInThreadId,
  isNewThread,
  available,
  quoteCount,
}: {
  text: string;
  threadId: string;
  optedInThreadId: string | null;
  isNewThread: boolean;
  available: boolean;
  quoteCount: number;
}): Record<string, unknown> | undefined {
  if (!isPptConfirmationRequest(text) || optedInThreadId !== threadId) {
    return undefined;
  }
  // Do not silently change an explicit choice while availability/history is
  // changing. The persisted first-run input is independently checked by Gateway.
  if (!isNewThread || !available || quoteCount !== 0) {
    throw new Error("ppt_confirmation_opt_in_unavailable");
  }
  return { ppt_confirmation_mode: "auto_after_60s" };
}
