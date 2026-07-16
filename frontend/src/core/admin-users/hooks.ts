import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createManagedUser,
  deleteManagedUser,
  listManagedUsers,
  resetManagedUserPassword,
  updateManagedUser,
} from "./api";
import type { CreateUserInput, ResetPasswordInput, UpdateUserInput } from "./types";

export const managedUsersQueryKey = ["admin-users"] as const;

function invalidateUsers(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: managedUsersQueryKey });
}

export function useManagedUsers(q: string) {
  return useQuery({
    queryKey: [...managedUsersQueryKey, q],
    queryFn: () => listManagedUsers(q),
  });
}

export function useCreateManagedUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateUserInput) => createManagedUser(input),
    onSuccess: () => invalidateUsers(queryClient),
  });
}

export function useUpdateManagedUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, input }: { userId: string; input: UpdateUserInput }) =>
      updateManagedUser(userId, input),
    onSuccess: () => invalidateUsers(queryClient),
  });
}

export function useResetManagedUserPassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      input,
    }: {
      userId: string;
      input: ResetPasswordInput;
    }) => resetManagedUserPassword(userId, input),
    onSuccess: () => invalidateUsers(queryClient),
  });
}

export function useDeleteManagedUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      userId,
      deleteFileData,
    }: {
      userId: string;
      deleteFileData: boolean;
    }) => deleteManagedUser(userId, deleteFileData),
    onSuccess: () => invalidateUsers(queryClient),
  });
}
