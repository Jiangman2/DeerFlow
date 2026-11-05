import { expect, test } from "@rstest/core";

import { getDashboardPage } from "@/core/dashboard/pagination";

test("getDashboardPage returns five rows and clamps stale page state", () => {
  const rows = Array.from({ length: 11 }, (_, index) => `run-${index + 1}`);

  expect(getDashboardPage(rows, 1)).toEqual({
    currentPage: 1,
    totalPages: 3,
    rows: ["run-6", "run-7", "run-8", "run-9", "run-10"],
  });
  expect(getDashboardPage(rows.slice(0, 3), 2)).toEqual({
    currentPage: 0,
    totalPages: 1,
    rows: ["run-1", "run-2", "run-3"],
  });
});
