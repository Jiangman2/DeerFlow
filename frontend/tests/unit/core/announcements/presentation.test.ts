import { expect, test } from "@rstest/core";

import {
  getAnnouncementDisplayStatus,
  validateAnnouncementWindow,
} from "@/core/announcements/presentation";

const now = Date.parse("2026-07-18T02:00:00Z");

test("published announcements distinguish scheduled, active, and ended states", () => {
  expect(
    getAnnouncementDisplayStatus(
      { status: "published", starts_at: "2026-07-18T03:00:00Z", ends_at: null },
      now,
    ),
  ).toBe("已排期");
  expect(
    getAnnouncementDisplayStatus(
      { status: "published", starts_at: null, ends_at: "2026-07-18T03:00:00Z" },
      now,
    ),
  ).toBe("展示中");
  expect(
    getAnnouncementDisplayStatus(
      { status: "published", starts_at: null, ends_at: "2026-07-18T02:00:00Z" },
      now,
    ),
  ).toBe("已结束");
});

test("draft and archived lifecycle states take precedence over schedule", () => {
  expect(
    getAnnouncementDisplayStatus(
      { status: "draft", starts_at: null, ends_at: null },
      now,
    ),
  ).toBe("草稿");
  expect(
    getAnnouncementDisplayStatus(
      { status: "archived", starts_at: null, ends_at: null },
      now,
    ),
  ).toBe("已归档");
});

test("announcement window validation accepts open and adjacent windows", () => {
  expect(validateAnnouncementWindow(null, null)).toBeNull();
  expect(validateAnnouncementWindow("2026-07-18T02:00", null)).toBeNull();
  expect(
    validateAnnouncementWindow("2026-07-18T02:00", "2026-07-18T03:00"),
  ).toBeNull();
});

test("announcement window validation rejects reversed and invalid windows", () => {
  expect(
    validateAnnouncementWindow("2026-07-18T03:00", "2026-07-18T02:00"),
  ).toBe("结束时间必须晚于开始时间");
  expect(validateAnnouncementWindow("invalid", "2026-07-18T02:00")).toBe(
    "时间格式无效",
  );
});
