import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

def error(identity):
    return AIMessage(id=identity,content='failure',additional_kwargs={'deerflow_error_fallback':True})

def complete(journal,message,tags=None):
    journal.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=message)]]),run_id=uuid4(),tags=tags or ['lead_agent'])

def reconciled(journal,messages):
    journal._reconcile_final_error_fallback({'messages':messages})
    return [e for e in journal._buffer if e['metadata'].get('reconciled')]

@pytest.mark.parametrize('boundary', [True, False])
def test_past_failure_then_success_is_not_rejournaled(boundary):
    journal=RunJournal('new-run','thread',MemoryRunEventStore(),flush_threshold=100)
    if boundary:journal.set_pre_existing_message_ids({'old-error'})
    answer=AIMessage(id='new-answer',content='delivered')
    complete(journal,answer)
    assert reconciled(journal,[error('old-error'),answer])==[]

def test_no_model_turn_does_not_replay_old_error():
    journal=RunJournal('new','thread',MemoryRunEventStore())
    assert reconciled(journal,[error('old')])==[]

def test_newer_success_does_not_reconcile_earlier_current_error():
    journal=RunJournal('new','thread',MemoryRunEventStore())
    journal.set_pre_existing_message_ids(set())
    assert reconciled(journal,[error('earlier'),AIMessage(id='latest',content='ok')])==[]

@pytest.mark.parametrize('boundary', [True,False])
def test_current_error_replacement_preserved(boundary):
    journal=RunJournal('new','thread',MemoryRunEventStore())
    if boundary:journal.set_pre_existing_message_ids({'old'})
    complete(journal,AIMessage(id='current',content=''))
    rows=reconciled(journal,[error('old'),error('current')])
    assert len(rows)==1 and rows[0]['content']['id']=='current'

def test_new_middleware_error_without_provider_callback_preserved():
    journal=RunJournal('new','thread',MemoryRunEventStore())
    journal.set_pre_existing_message_ids({'old'})
    rows=reconciled(journal,[error('old'),error('middleware-new')])
    assert len(rows)==1 and rows[0]['content']['id']=='middleware-new'

def test_boundary_is_copied_and_old_callback_cannot_override_it():
    journal=RunJournal('new','thread',MemoryRunEventStore())
    ids={'old'};journal.set_pre_existing_message_ids(ids);ids.clear()
    complete(journal,error('old'))
    assert reconciled(journal,[error('old')])==[]

def test_without_boundary_subagent_is_not_lead_authority():
    journal=RunJournal('new','thread',MemoryRunEventStore())
    complete(journal,AIMessage(id='sub',content='hi'),['subagent:test'])
    assert reconciled(journal,[error('sub')])==[]

def test_empty_warning_has_no_reasoning_or_provider_secrets(caplog):
    journal=RunJournal('new','thread',MemoryRunEventStore())
    with caplog.at_level(logging.WARNING):
        complete(journal,AIMessage(id='empty',content='',additional_kwargs={'reasoning_content':'PRIVATE_REASONING'},response_metadata={'private':'PRIVATE_KEY'}))
    assert 'LLM_EMPTY_RESPONSE' in caplog.text
    assert 'reasoning_chars=17' in caplog.text
    assert 'PRIVATE_REASONING' not in caplog.text and 'PRIVATE_KEY' not in caplog.text

def test_tool_only_reply_is_not_empty_warning(caplog):
    journal=RunJournal('new','thread',MemoryRunEventStore())
    with caplog.at_level(logging.WARNING):
        complete(journal,AIMessage(id='tool',content='',tool_calls=[{'id':'tool1','name':'bash','args':{}}]))
    assert 'LLM_EMPTY_RESPONSE' not in caplog.text

@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['existing','new','failed','absent'])
async def test_worker_binds_checkpoint_boundary_before_execution(monkeypatch,mode):
    from unittest.mock import AsyncMock
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.worker import RunContext,run_agent
    import deerflow.runtime.runs.worker as worker
    monkeypatch.setattr(worker,'capture_workspace_snapshot',AsyncMock(return_value={}))
    manager=RunManager();record=await manager.create('thread-scope')
    bridge=SimpleNamespace(publish=AsyncMock(),publish_end=AsyncMock(),cleanup=AsyncMock())
    async def checkpoint(config):
        if mode=='failed':raise RuntimeError('test read failure')
        if mode=='new':return None
        return SimpleNamespace(config={},checkpoint={'channel_values':{'messages':[error('old')]}},metadata={},pending_writes=[])
    saver=None if mode=='absent' else SimpleNamespace(aget_tuple=checkpoint)
    observed=[]
    class Agent:
        async def astream(self,graph_input,config=None,**kwargs):
            journal=next(c for c in config['callbacks'] if isinstance(c,RunJournal))
            observed.append(journal._pre_existing_ai_ids)
            yield {'messages':[]}
    await run_agent(bridge,manager,record,ctx=RunContext(checkpointer=saver,event_store=MemoryRunEventStore()),
                    agent_factory=lambda **kwargs:Agent(),graph_input={},config={})
    assert observed==[{'old'} if mode=='existing' else set() if mode=='new' else None]
