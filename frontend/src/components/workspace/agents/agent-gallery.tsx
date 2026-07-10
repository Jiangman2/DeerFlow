"use client";

import { BotIcon, PlusIcon, StoreIcon, UserIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AgentsApiDisabledError,
  SHARED_EXTERNAL_AGENTS,
  SHARED_EXTERNAL_AGENT_NAME_ALIASES,
  useAgents,
  useTeamAgents,
} from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

import { AgentCard } from "./agent-card";

export function AgentGallery() {
  const { t } = useI18n();
  const { agents, isLoading, error } = useAgents();
  const {
    agents: teamAgents,
    isLoading: isTeamLoading,
    error: teamError,
  } = useTeamAgents();
  const router = useRouter();
  const agentsApiDisabled = error instanceof AgentsApiDisabledError;
  const teamAgentsApiDisabled = teamError instanceof AgentsApiDisabledError;
  const sharedAgentNames = new Set(
    SHARED_EXTERNAL_AGENTS.map((agent) => agent.name).concat(
      SHARED_EXTERNAL_AGENT_NAME_ALIASES,
    ),
  );
  const squareAgents = [
    ...SHARED_EXTERNAL_AGENTS,
    ...teamAgents.filter((agent) => !sharedAgentNames.has(agent.name)),
  ];

  const handleNewAgent = () => {
    router.push("/workspace/agents/new");
  };

  return (
    <Tabs defaultValue="mine" className="flex size-full gap-0">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.agents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.agents.description}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <TabsList>
            <TabsTrigger value="mine">
              <UserIcon className="h-4 w-4" />
              {t.agents.myAgents}
            </TabsTrigger>
            <TabsTrigger value="square">
              <StoreIcon className="h-4 w-4" />
              {t.agents.agentSquare}
            </TabsTrigger>
          </TabsList>
          <Button onClick={handleNewAgent}>
            <PlusIcon className="mr-1.5 h-4 w-4" />
            {t.agents.newAgent}
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6">
        <>
          <TabsContent value="mine">
            {agentsApiDisabled ? (
              <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
                <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
                  <BotIcon className="text-muted-foreground h-7 w-7" />
                </div>
                <div>
                  <p className="font-medium">{t.agents.apiDisabledTitle}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {t.agents.apiDisabledDescription}
                  </p>
                </div>
              </div>
            ) : (
              <>
                {isLoading ? (
                  <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                    {t.common.loading}
                  </div>
                ) : agents.length === 0 ? (
                  <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
                    <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
                      <BotIcon className="text-muted-foreground h-7 w-7" />
                    </div>
                    <div>
                      <p className="font-medium">{t.agents.emptyTitle}</p>
                      <p className="text-muted-foreground mt-1 text-sm">
                        {t.agents.emptyDescription}
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      className="mt-2"
                      onClick={handleNewAgent}
                    >
                      <PlusIcon className="mr-1.5 h-4 w-4" />
                      {t.agents.newAgent}
                    </Button>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
                    {agents.map((agent) => (
                      <AgentCard key={agent.name} agent={agent} showPublish />
                    ))}
                  </div>
                )}
              </>
            )}
          </TabsContent>
          <TabsContent value="square">
            {isTeamLoading && squareAgents.length === 0 ? (
              <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
                {t.common.loading}
              </div>
            ) : squareAgents.length === 0 ? (
              <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
                <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
                  <StoreIcon className="text-muted-foreground h-7 w-7" />
                </div>
                <div>
                  <p className="font-medium">{t.agents.squareEmptyTitle}</p>
                  <p className="text-muted-foreground mt-1 max-w-md text-sm">
                    {t.agents.squareEmptyDescription}
                  </p>
                </div>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {squareAgents.map((agent) => (
                  <AgentCard
                    key={agent.name}
                    agent={agent}
                    showDelete={false}
                    showUnpublish={
                      !teamAgentsApiDisabled &&
                      agent.external_url == null &&
                      agent.published_by_current_user === true
                    }
                  />
                ))}
              </div>
            )}
          </TabsContent>
        </>
      </div>
    </Tabs>
  );
}
