import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  createAgent,
  deleteAgent,
  deleteAgentAvatar,
  fetchAgentsApiEnabled,
  getAgent,
  listAgents,
  listDigitalEmployeeCategories,
  listTeamAgents,
  publishAgent,
  unpublishTeamAgent,
  uploadAgentAvatar,
  updateAgent,
} from "./api";
import {
  readCachedAgentsApiEnabled,
  resolveAgentsApiEnabled,
  writeCachedAgentsApiEnabled,
} from "./feature-cache";
import type { CreateAgentRequest, UpdateAgentRequest } from "./types";

export function useAgentsApiEnabled() {
  const { data, isPending } = useQuery({
    queryKey: ["features", "agents_api"],
    queryFn: () => fetchAgentsApiEnabled(),
    // Re-check on every mount so flipping config.yaml + revisiting the
    // agents section auto-enables the feature without a rebuild.
    staleTime: 0,
    refetchOnMount: true,
    retry: false,
  });

  // localStorage only exists in the browser, so read the last-known value
  // after mount (not during render). This keeps the first client render equal
  // to the server's (cache unknown → fail open), avoiding a hydration mismatch
  // on the non-loading-gated sidebar; the sticky value is applied on the next
  // render.
  const [cached, setCached] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    setCached(readCachedAgentsApiEnabled());
  }, []);

  // Persist every definitive answer so a cold start during an /api/features
  // outage can fall back to it instead of failing open and re-introducing the
  // 403 storm (#3757).
  useEffect(() => {
    if (data !== undefined) {
      writeCachedAgentsApiEnabled(data);
      setCached(data);
    }
  }, [data]);

  // A live answer wins; otherwise stay on the last-known value (sticky) and
  // only fail open when nothing has ever been observed.
  return {
    enabled: resolveAgentsApiEnabled(data, cached),
    isLoading: isPending,
  };
}

export function useAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(),
  });
  return { agents: data ?? [], isLoading, error };
}

export function useAgent(name: string | null | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });
  return { agent: data ?? null, isLoading, error };
}

export function useTeamAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["team-agents"],
    queryFn: () => listTeamAgents(),
  });
  return { agents: data ?? [], isLoading, error };
}

export function useDigitalEmployeeCategories() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["digital-employee-categories"],
    queryFn: () => listDigitalEmployeeCategories(),
    staleTime: 60_000,
  });
  return { categories: data ?? [], isLoading, error };
}

export function useCreateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: CreateAgentRequest) => createAgent(request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useUpdateAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      request,
    }: {
      name: string;
      request: UpdateAgentRequest;
    }) => updateAgent(name, request),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}

export function useDeleteAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteAgent(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function usePublishAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => publishAgent(name),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["team-agents"] }),
        queryClient.invalidateQueries({ queryKey: ["employee-directory"] }),
      ]);
    },
  });
}

export function useUnpublishTeamAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unpublishTeamAgent(name),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["team-agents"] }),
        queryClient.invalidateQueries({ queryKey: ["employee-directory"] }),
      ]);
    },
  });
}

export function useUploadAgentAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, file }: { name: string; file: File }) =>
      uploadAgentAvatar(name, file),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}

export function useDeleteAgentAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteAgentAvatar,
    onSuccess: (_data, name) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}
