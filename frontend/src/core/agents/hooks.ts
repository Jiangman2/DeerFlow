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
  shouldRetryAgentLoad,
  unpublishTeamAgent,
  uploadAgentAvatar,
  updateAgent,
} from "./api";
import {
  readCachedAgentsApiEnabled,
  resolveAgentsApiEnabled,
  writeCachedAgentsApiEnabled,
} from "./feature-cache";
import type {
  AgentSource,
  CreateAgentRequest,
  UpdateAgentRequest,
} from "./types";

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

export function useAgent(
  name: string | null | undefined,
  source?: AgentSource,
) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents", name, source],
    queryFn: () => getAgent(name!, source),
    enabled: !!name,
    retry: shouldRetryAgentLoad,
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
    retry: false,
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
    retry: false,
    mutationFn: ({ name, expectedDraftRevision }: { name: string; expectedDraftRevision: string }) => deleteAgent(name, expectedDraftRevision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function usePublishAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      expectedRevision,
      expectedDraftRevision,
      grantIds,
    }: {
      name: string;
      expectedRevision?: string | null;
      expectedDraftRevision: string;
      grantIds: string[];
    }) => publishAgent(name, expectedRevision, expectedDraftRevision, grantIds),
    retry: false,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["team-agents"] });
    },
  });
}

export function useUnpublishTeamAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      name,
      expectedRevision,
    }: {
      name: string;
      expectedRevision?: string | null;
    }) => unpublishTeamAgent(name, expectedRevision),
    retry: false,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["team-agents"] });
    },
  });
}

export function useUploadAgentAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: ({ name, file, expectedDraftRevision }: { name: string; file: File; expectedDraftRevision: string }) =>
      uploadAgentAvatar(name, file, expectedDraftRevision),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}

export function useDeleteAgentAvatar() {
  const queryClient = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: ({ name, expectedDraftRevision }: { name: string; expectedDraftRevision: string }) => deleteAgentAvatar(name, expectedDraftRevision),
    onSuccess: (_data, { name }) => {
      void queryClient.invalidateQueries({ queryKey: ["agents"] });
      void queryClient.invalidateQueries({ queryKey: ["agents", name] });
    },
  });
}
