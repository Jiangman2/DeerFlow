import { expect, rs, test } from "@rstest/core";

const invalidate = rs.hoisted(() => rs.fn().mockResolvedValue(undefined));
rs.mock("@tanstack/react-query", () => ({
  useQuery: rs.fn(),
  useQueryClient: () => ({ invalidateQueries: invalidate }),
  useMutation: (options: unknown) => options,
}));

import { usePublishAgent } from "@/core/agents/hooks";

test("successful publication refreshes both the shared snapshot and withdrawn-state caches", async () => {
  const mutation = usePublishAgent() as unknown as {
    onSuccess: () => Promise<void>;
  };
  await mutation.onSuccess();
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["team-agents"] });
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ["square-settings"] });
});
