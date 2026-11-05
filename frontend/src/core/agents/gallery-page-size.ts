/** Keep desktop cards readable; mobile keeps five cards and natural scrolling. */
export function galleryPageSize(
  height: number,
  desktop: boolean,
  rowHeight = 112,
) {
  if (!desktop || !Number.isFinite(height) || height <= 0) return 5;
  const gap = 10;
  return Math.max(
    1,
    Math.min(50, Math.floor((height + gap) / (rowHeight + gap))),
  );
}
