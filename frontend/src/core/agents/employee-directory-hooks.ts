import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";

import { employeeSource, type EmployeeMetadata } from "./employee-directory";
import type { Agent } from "./types";

export function useEmployeeDirectory() {
  const { user } = useAuth();
  const client = useQueryClient();
  const queryKey = ["employee-directory", user?.id];
  const query = useQuery({
    queryKey,
    enabled: !!user,
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: true,
    queryFn: async () => {
      const response = await fetch(
        `${getBackendBaseURL()}/api/employee-directory`,
      );
      if (!response.ok) throw new Error("Employee directory unavailable");
      const data = (await response.json()) as { employees: EmployeeMetadata[] };
      return data.employees;
    },
  });
  const update = useMutation({
    mutationFn: async (agent: Agent) => {
      if (!agent.hot_identity) throw new Error("save_failed");
      const source = employeeSource(agent);
      const response = await fetch(
        `${getBackendBaseURL()}/api/employee-directory/${source}/${encodeURIComponent(agent.name)}/hot`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            is_hot: !agent.is_hot,
            expected_version: agent.hot_version ?? 0,
            expected_identity: agent.hot_identity,
          }),
        },
      );
      if (!response.ok)
        throw new Error(response.status === 409 ? "conflict" : "save_failed");
    },
    onSettled: async () => {
      await client.invalidateQueries({ queryKey });
    },
  });
  return {
    ...query,
    entries: query.data ?? [],
    update,
    isAdmin: user?.system_role === "admin",
  };
}
