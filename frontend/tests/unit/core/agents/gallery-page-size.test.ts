import { describe, expect, it } from "@rstest/core";

import { galleryPageSize } from "@/core/agents/gallery-page-size";

describe("employee gallery viewport pagination", () => {
  it("fits readable desktop cards in short and tall windows without a five-card cap", () => {
    expect(galleryPageSize(600, true)).toBe(5);
    expect(galleryPageSize(360, true)).toBe(3);
    expect(galleryPageSize(1000, true)).toBe(8);
    expect(galleryPageSize(90, true)).toBe(1);
    expect(galleryPageSize(1000, true, 160)).toBe(5);
  });
  it("keeps five mobile cards with natural scrolling and handles pre-layout values", () => {
    expect(galleryPageSize(600, false)).toBe(5);
    expect(galleryPageSize(0, true)).toBe(5);
    expect(galleryPageSize(Number.NaN, true)).toBe(5);
  });
});
