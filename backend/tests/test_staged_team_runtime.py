"""Run in the candidate's backend test environment, in addition to existing stage tests."""
from types import SimpleNamespace
from langchain_core.messages import HumanMessage
from deerflow.config.staged_execution_config import StagedExecutionConfig
from deerflow.agents.middlewares.staged_execution_middleware import StagedExecutionMiddleware
from test_staged_execution import make_graph,invoke,sequential_responses

def middleware(target='policy'):
    return StagedExecutionMiddleware(StagedExecutionConfig(enabled=True,team_agents=['policy']),agent_target=target)

def test_team_actual_graph_without_thread_allowlist():
    graph,_=make_graph(sequential_responses(),middleware=middleware())
    result=invoke(graph,thread='new-team-thread')
    assert result['staged_execution']['status']=='completed'
    assert result['staged_execution']['agent_target']=='policy'

def test_context_cannot_self_enable_team_scope():
    mw=middleware(None)
    runtime=SimpleNamespace(context={'run_id':'r','thread_id':'t','agent_name':'policy','agent_target':'policy','staged_execution':True})
    assert mw.before_agent({'messages':[]},runtime) is None

def test_team_target_preserves_skill_ownership():
    runtime=SimpleNamespace(context={'run_id':'r','thread_id':'t'})
    assert middleware().before_agent({'messages':[HumanMessage(content='/ppt-master create')]},runtime) is None

def test_checkpoint_not_reused_for_another_target():
    mw=middleware()
    runtime=SimpleNamespace(context={'run_id':'r2','thread_id':'t'})
    previous={'run_id':'r1','agent_target':'other','stages':[{}], 'status':'active'}
    result=mw.before_agent({'messages':[HumanMessage(content='继续')],'staged_execution':previous},runtime)
    assert result['staged_execution']['stages']==[]
    assert result['staged_execution']['agent_target']=='policy'
