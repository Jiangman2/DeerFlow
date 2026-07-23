import { describe, expect, it } from "@rstest/core";

import {
  isJoinedKnowledgeBase,
  isOwnedKnowledgeBase,
  isTeamOrPublicKnowledgeBase,
} from "@/core/weknora/classification";

describe("WeKnora knowledge-base classification", () => {
  it("keeps ownership and team sharing as independent dimensions", () => {
    const knowledgeBase = {
      id: "owned-shared",
      name: "Owned and shared",
      access_scope: "owned",
      is_owner: true,
      is_joined: false,
      is_team_or_public: true,
      share_count: 1,
    };

    expect(isOwnedKnowledgeBase(knowledgeBase)).toBe(true);
    expect(isJoinedKnowledgeBase(knowledgeBase)).toBe(false);
    expect(isTeamOrPublicKnowledgeBase(knowledgeBase)).toBe(true);
  });

  it("classifies knowledge bases created by another member as joined", () => {
    const knowledgeBase = {
      id: "joined-tenant",
      name: "Joined tenant",
      access_scope: "joined",
      is_owner: false,
      is_joined: true,
      is_team_or_public: false,
    };

    expect(isOwnedKnowledgeBase(knowledgeBase)).toBe(false);
    expect(isJoinedKnowledgeBase(knowledgeBase)).toBe(true);
    expect(isTeamOrPublicKnowledgeBase(knowledgeBase)).toBe(false);
  });

  it("counts organization shares as both joined and team knowledge", () => {
    const knowledgeBase = {
      id: "team-share",
      name: "Team share",
      access_scope: "joined",
      is_owner: false,
      is_joined: true,
      is_team_or_public: true,
      organization_id: "org-1",
    };

    expect(isJoinedKnowledgeBase(knowledgeBase)).toBe(true);
    expect(isTeamOrPublicKnowledgeBase(knowledgeBase)).toBe(true);
  });
});
