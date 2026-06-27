import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createAgent,
  deleteAgent,
  getAgent,
  getTeamAgent,
  listAgents,
  listTeamAgents,
  publishAgent,
  unpublishTeamAgent,
  updateAgent,
} from "./api";
import type { CreateAgentRequest, UpdateAgentRequest } from "./types";

export function useAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["agents"],
    queryFn: () => listAgents(),
  });
  return { agents: data ?? [], isLoading, error };
}

export function useTeamAgents() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["team-agents"],
    queryFn: () => listTeamAgents(),
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

export function useTeamAgent(name: string | null | undefined) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["team-agents", name],
    queryFn: () => getTeamAgent(name!),
    enabled: !!name,
  });
  return { agent: data ?? null, isLoading, error };
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team-agents"] });
    },
  });
}

export function useUnpublishTeamAgent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => unpublishTeamAgent(name),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["team-agents"] });
    },
  });
}
