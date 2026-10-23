import { describe, expect, test } from "@rstest/core";

import {
  confirmationSubmission,
  isPptConfirmationRequest,
} from "@/core/ppt-confirmation/consent";

const initial = {
  text: "/ppt-master 做一个PPT",
  threadId: "new-ppt",
  optedInThreadId: null as string | null,
  isNewThread: true,
  available: true,
  quoteCount: 0,
};

describe("task-local PPT automatic confirmation", () => {
  test("default is manual without persistent consent metadata", () => {
    expect(confirmationSubmission(initial)).toBeUndefined();
  });
  test("only explicit current-thread intent emits the first-input marker", () => {
    expect(
      confirmationSubmission({ ...initial, optedInThreadId: "new-ppt" }),
    ).toEqual({
      ppt_confirmation_mode: "auto_after_60s",
    });
  });
  test("new thread and other skills never inherit the previous task choice", () => {
    expect(
      confirmationSubmission({ ...initial, optedInThreadId: "old-ppt" }),
    ).toBeUndefined();
    expect(
      confirmationSubmission({
        ...initial,
        optedInThreadId: "new-ppt",
        text: "/other make PPT",
      }),
    ).toBeUndefined();
  });
  test.each([{ isNewThread: false }, { available: false }, { quoteCount: 1 }])(
    "changed admission cannot silently downgrade a chosen automatic mode: %j",
    (change) => {
      expect(() =>
        confirmationSubmission({
          ...initial,
          ...change,
          optedInThreadId: "new-ppt",
        }),
      ).toThrow("ppt_confirmation_opt_in_unavailable");
    },
  );
  test("explicit slash has a complete command boundary", () => {
    expect(isPptConfirmationRequest("  /PPT-MASTER\nmake slides")).toBe(true);
    expect(isPptConfirmationRequest("/ppt-master-other")).toBe(false);
    expect(isPptConfirmationRequest("制作一个PPT")).toBe(false);
    expect(isPptConfirmationRequest("please use /ppt-master")).toBe(false);
  });
});
