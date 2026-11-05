/** Keep the number of mounted per-member controls independent of roster size. */
export function memberPageWindow(total: number, page: number, pageSize: number) {
  const size = [20, 50, 100].includes(pageSize) ? pageSize : 20
  const pages = Math.max(1, Math.ceil(total / size))
  const current = Number.isFinite(page) ? Math.min(pages, Math.max(1, Math.floor(page))) : 1
  const start = (current - 1) * size
  return { page: current, start, end: Math.min(total, start + size) }
}
