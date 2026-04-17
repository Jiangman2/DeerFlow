import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { enableSkill } from "./api";
import type { Skill } from "./type";

import { loadSkills } from ".";

export function useSkills() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["skills"],
    queryFn: () => loadSkills(),
  });
  return { skills: data ?? [], isLoading, error };
}

export function useEnableSkill() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      skillName,
      enabled,
    }: {
      skillName: string;
      enabled: boolean;
    }) => {
      await enableSkill(skillName, enabled);
    },
    onMutate: async ({ skillName, enabled }) => {
      await queryClient.cancelQueries({ queryKey: ["skills"] });
      const previous = queryClient.getQueryData<Skill[]>(["skills"]);
      queryClient.setQueryData<Skill[]>(["skills"], (old) => {
        if (!old) return old;
        return old.map((s) => (s.name === skillName ? { ...s, enabled } : s));
      });
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["skills"], context.previous);
      }
      const message =
        _error instanceof Error ? _error.message : "Failed to update skill.";
      toast.error(message);
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });
}
