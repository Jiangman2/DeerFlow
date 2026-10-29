import { z } from "zod";

export const competitionBoardSchema = z.object({
  title: z.string(),
  updated_at: z.string().datetime({ offset: true }),
  unavailable_count: z.number().int().nonnegative(),
  refresh_seconds: z.number(),
  items: z.array(
    z.object({
      identity: z.string(),
      name: z.string(),
      total_clicks: z.number().int().nonnegative(),
      today_clicks: z.number().int().nonnegative(),
    }),
  ),
});

export type CompetitionBoard = z.infer<typeof competitionBoardSchema>;
export interface CompetitionEmployee {
  identity: string;
  publication: string;
  name: string;
  source?: string;
}
export interface CompetitionConfig {
  revision: number;
  title: string;
  enabled: boolean;
  employees: CompetitionEmployee[];
}
