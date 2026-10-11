import { expect, rs, test } from "@rstest/core";

const invalidate = rs.hoisted(() => rs.fn().mockResolvedValue(undefined));
const query = rs.hoisted(() => rs.fn());
rs.mock("@tanstack/react-query", () => ({
  useQuery: query,
  useQueryClient: () => ({ invalidateQueries: invalidate }),
  useMutation: (options: unknown) => options,
}));

import { usePublishAgent, useTeamAgents } from "@/core/agents/hooks";

test("successful publication refreshes both the shared snapshot and withdrawn-state caches", async () => {
  const mutation = usePublishAgent() as unknown as {
    onSuccess: () => Promise<void>;
  };
  await mutation.onSuccess();
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["team-agents"] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["square-settings"] });
});

test("team-agent reads expose refetch for the square retry action", () => {
  const refetch = rs.fn().mockResolvedValue({ data: [] });
  query.mockReturnValue({ data: [], isLoading: false, error: new Error("offline"), refetch });
  expect(useTeamAgents(true).refetch).toBe(refetch);
});
