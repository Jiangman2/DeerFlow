export function GET() {
  return Response.json({
    private: {
      threads: [],
      total: 0,
    },
    groups: [],
    groups_total: 0,
  });
}