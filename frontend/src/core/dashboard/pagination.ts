export function getDashboardPage<T>(
  items: readonly T[],
  page: number,
  pageSize = 5,
): {
  currentPage: number;
  totalPages: number;
  rows: T[];
} {
  const safePageSize = Math.max(1, Math.floor(pageSize));
  const totalPages = Math.max(1, Math.ceil(items.length / safePageSize));
  const currentPage = Math.min(Math.max(0, Math.floor(page)), totalPages - 1);
  return {
    currentPage,
    totalPages,
    rows: items.slice(
      currentPage * safePageSize,
      (currentPage + 1) * safePageSize,
    ),
  };
}
