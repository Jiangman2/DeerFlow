import { useQuery } from "@tanstack/react-query";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  loadEmployeeInvocationSkills,
  type EmployeeSkillScope,
} from "./invocation";

export function useEmployeeInvocationSkills(scope?: EmployeeSkillScope) {
  const { user } = useAuth();
  const query = useQuery({
    queryKey: [
      "employee-invocation-skills",
      user?.id,
      scope?.name,
      scope?.source,
      scope?.threadId ?? null,
    ],
    queryFn: ({ signal }) => {
      if (!scope || !user) throw new Error("Employee skill context required");
      return loadEmployeeInvocationSkills(scope, signal);
    },
    enabled: Boolean(scope && user),
    retry: false,
    staleTime: 0,
  });
  // A failed revalidation must not continue offering a cached revoked catalog.
  return {
    skills: query.isError ? [] : (query.data ?? []),
    error: query.isError,
    isLoading: query.isLoading,
  };
}
