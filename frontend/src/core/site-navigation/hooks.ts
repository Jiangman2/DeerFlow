import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createSiteNavigation,
  deleteSiteNavigation,
  listAdminSiteNavigation,
  listSiteNavigation,
  updateSiteNavigation,
} from "./api";
import type { SiteNavigationInput } from "./types";

export const siteNavigationQueryKey = ["siteNavigation"] as const;
export const adminSiteNavigationQueryKey = ["adminSiteNavigation"] as const;

function invalidateSiteNavigation(
  queryClient: ReturnType<typeof useQueryClient>,
) {
  void queryClient.invalidateQueries({ queryKey: siteNavigationQueryKey });
  void queryClient.invalidateQueries({ queryKey: adminSiteNavigationQueryKey });
}

export function useSiteNavigation() {
  const { data, isLoading, error } = useQuery({
    queryKey: siteNavigationQueryKey,
    queryFn: listSiteNavigation,
    staleTime: 60_000,
  });
  return { sites: data ?? [], isLoading, error };
}

export function useAdminSiteNavigation() {
  const { data, isLoading, error } = useQuery({
    queryKey: adminSiteNavigationQueryKey,
    queryFn: listAdminSiteNavigation,
  });
  return { sites: data ?? [], isLoading, error };
}

export function useCreateSiteNavigation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SiteNavigationInput) => createSiteNavigation(input),
    onSuccess: () => invalidateSiteNavigation(queryClient),
  });
}

export function useUpdateSiteNavigation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input: Partial<SiteNavigationInput>;
    }) => updateSiteNavigation(id, input),
    onSuccess: () => invalidateSiteNavigation(queryClient),
  });
}

export function useDeleteSiteNavigation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSiteNavigation(id),
    onSuccess: () => invalidateSiteNavigation(queryClient),
  });
}
