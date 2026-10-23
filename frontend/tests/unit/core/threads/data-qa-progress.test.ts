import { expect, test } from "@rstest/core";

import {
  mergeDataQAProgress,
  settleDataQAProgress,
  dataQAResultStage,
} from "@/core/threads/data-qa-progress";

const event = {
  type: "data_qa_progress",
  tool: "datalake_data_qa",
  thread_id: "t",
  run_id: "r",
  tool_call_id: "c",
  sequence: 1,
  stage: "waiting",
};
const key = JSON.stringify(["t", "c"]);

test("scopes concurrent calls and ignores old runs, malformed and raw events", () => {
  const one = mergeDataQAProgress({}, event, "r");
  const two = mergeDataQAProgress(
    one,
    { ...event, tool_call_id: "other" },
    "r",
  );
  expect(Object.keys(two)).toHaveLength(2);
  expect(mergeDataQAProgress(two, { ...event, run_id: "old" }, "r")).toBe(two);
  expect(
    mergeDataQAProgress(two, { ...event, stage: "private SQL" }, "r"),
  ).toBe(two);
  expect(mergeDataQAProgress(two, { ...event, sequence: NaN }, "r")).toBe(two);
  expect(
    JSON.stringify(mergeDataQAProgress({}, { ...event, raw: "secret" }, "r")),
  ).not.toContain("secret");
});

test("deduplicates replay and never reopens a terminal call", () => {
  const waiting = mergeDataQAProgress({}, event, "r");
  expect(mergeDataQAProgress(waiting, event, "r")).toBe(waiting);
  const done = mergeDataQAProgress(
    waiting,
    { ...event, sequence: 3, stage: "completed" },
    "r",
  );
  expect(
    mergeDataQAProgress(
      done,
      { ...event, sequence: 4, stage: "processing" },
      "r",
    ),
  ).toBe(done);
  expect(done[key]?.stages).toEqual(["waiting", "completed"]);
});

test("cancel and error stop loading, other runs and successful results stay intact", () => {
  const waiting = mergeDataQAProgress({}, event, "r");
  expect(
    settleDataQAProgress(waiting, "cancelled", "other")[key]?.stages,
  ).toEqual(["waiting"]);
  const ended = settleDataQAProgress(waiting, "stopped", "r");
  expect(
    settleDataQAProgress(ended, "cancelled", "r")[key]?.stages.at(-1),
  ).toBe("cancelled");
  expect(settleDataQAProgress(waiting, "failed", "r")[key]?.stages.at(-1)).toBe(
    "failed",
  );
  expect(dataQAResultStage({ status: "done" })).toBe("completed");
  expect(dataQAResultStage('{"status":"interrupted"}')).toBe("clarification");
  expect(dataQAResultStage({ error: "hidden", code: "ReadTimeout" })).toBe(
    "timeout",
  );
  expect(dataQAResultStage(undefined)).toBeUndefined();
});

test("a new run may reuse a call id without inheriting steps; storage is bounded", () => {
  let state = mergeDataQAProgress({}, event, "r");
  state = mergeDataQAProgress(state, { ...event, run_id: "new" }, "new");
  expect(state[key]?.runId).toBe("new");
  expect(state[key]?.stages).toEqual(["waiting"]);
  for (let i = 0; i < 200; i++)
    state = mergeDataQAProgress(
      state,
      { ...event, tool_call_id: String(i) },
      "r",
    );
  expect(Object.keys(state)).toHaveLength(100);
});
