import { useQuery } from "@tanstack/react-query";

import { fetchBrowserControlEnabled, fetchPptConfirmationEnabled } from "./api";

export function useBrowserControlEnabled() {
  const { data, isPending } = useQuery({
    queryKey: ["features", "browser_control"],
    queryFn: () => fetchBrowserControlEnabled(),
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });

  return {
    enabled: data ?? false,
    isLoading: isPending,
  };
}

export function usePptConfirmationEnabled(eligible: boolean) {
  const { data, isError } = useQuery({
    queryKey: ["features", "ppt_confirmation"],
    queryFn: fetchPptConfirmationEnabled,
    enabled: eligible,
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });
  return eligible && !isError && data === true;
}
