import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createAnnouncement,
  getActiveAnnouncement,
  listAnnouncements,
  setAnnouncementStatus,
  updateAnnouncement,
} from "./api";
import type { AnnouncementInput, AnnouncementStatus } from "./types";

export const activeAnnouncementQueryKey = ["activeAnnouncement"] as const;
export const announcementsQueryKey = ["announcements"] as const;

function invalidateAnnouncements(
  queryClient: ReturnType<typeof useQueryClient>,
) {
  void queryClient.invalidateQueries({ queryKey: activeAnnouncementQueryKey });
  void queryClient.invalidateQueries({ queryKey: announcementsQueryKey });
}

export function useActiveAnnouncement() {
  return useQuery({
    queryKey: activeAnnouncementQueryKey,
    queryFn: getActiveAnnouncement,
    staleTime: 60_000,
    refetchInterval: 120_000,
  });
}

export function useAnnouncements() {
  const { data, isLoading, error } = useQuery({
    queryKey: announcementsQueryKey,
    queryFn: listAnnouncements,
  });
  return { announcements: data ?? [], isLoading, error };
}

export function useCreateAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AnnouncementInput) => createAnnouncement(input),
    onSuccess: () => invalidateAnnouncements(queryClient),
  });
}

export function useUpdateAnnouncement() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input: Partial<AnnouncementInput>;
    }) => updateAnnouncement(id, input),
    onSuccess: () => invalidateAnnouncements(queryClient),
  });
}

export function useSetAnnouncementStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: AnnouncementStatus }) =>
      setAnnouncementStatus(id, status),
    onSuccess: () => invalidateAnnouncements(queryClient),
  });
}
