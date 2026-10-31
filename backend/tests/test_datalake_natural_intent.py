"""Natural query eligibility never grants access or permits external fallback."""
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from deerflow.runtime.datalake_policy import query_scope, STATE_KEY
from deerflow.agents.middlewares.datalake_source_middleware import DataLakeSourceMiddleware


QUERIES = [
    '天津公司有多少气田', '天津公司有几个气田？', '天津公司下属气田有哪些',
    '列出天津公司管理的气田清单', '某气田属于哪个公司', '某油田有哪些井',
    '统计某油田上个月产量', '天津公司2025年产量是多少',
    '对比天津公司今年和去年的产气量', '看看某平台的生产明细',
    '某油田的含水率是多少', '查一下某井的井深和坐标',
    '天津公司的年度指标完成率怎么样', '查 NB27-1-1 的井史',
    '检索某油田的完井报告', '帮我查一下 NB27-1-1 井身结构',
    '调取某平台的生产日报', '查一下某油田产量并生成Word',
    '从数据湖查询某油田产量', '统计公司内部数据表中的业务指标',
]


@pytest.mark.parametrize('text', QUERIES)
def test_business_fact_needs_no_magic_source_word(text):
    assert {"datalake_data_qa", "datalake_smart_qa"} <= visible(text)
    scope = query_scope({'messages': [HumanMessage(text)]}, {'datalake_enabled': True})
    assert not scope['required'] and scope['trigger'] == 'model_decision'


@pytest.mark.parametrize('text', [
    '天津公司有多少气田这句话翻译成英文', '帮我写一篇气田统计方法的论文',
    '生成天津公司的气田清单模板', '给我设计天津公司的气田统计报表模板',
    '模拟天津公司各气田的产量数据', '统计上传Excel中的天津公司气田数量',
    '列出这份表格中天津公司的气田', '分析附件中的完井报告',
    '什么是气田', '解释产量指标的计算方法', '介绍数据湖查询原理',
    '帮我分析数据湖工具查询失败的故障原因', '不要查询天津公司产量',
    '不用数据湖，分析上传文件中的产量', '查询全球气田数量',
    '根据公开数据统计天津公司产量', '联网搜索某油田的相关新闻',
    '查询今天的天气', '查看这份文件', '帮我统计一下',
    '修改下列文字：\n> 天津公司有多少气田', '“天津公司有多少气田”',
    '审查代码 ```python\nquery("天津公司有多少气田")\n```',
])
def test_wording_alone_neither_hides_tools_nor_establishes_source_scope(text):
    assert {"datalake_data_qa", "datalake_smart_qa"} <= visible(text)
    assert not query_scope({"messages": [HumanMessage(text)]})["required"]


def request(text, enabled, name='datalake_data_qa'):
    return SimpleNamespace(state={'messages': [HumanMessage(text)]},
                           runtime=SimpleNamespace(context={'datalake_enabled': enabled}),
                           tool_call={'id': 't', 'name': name, 'args': {'query': text}})


@pytest.mark.parametrize('enabled', [True, False, None, 'true'])
@pytest.mark.parametrize('tool,text', [('datalake_data_qa', QUERIES[0]), ('datalake_smart_qa', '查 NB27-1-1 的井史')])
def test_authorization_and_external_boundaries_preserved(enabled, tool, text):
    middleware = DataLakeSourceMiddleware()
    req = request(text, enabled, tool)
    denied = middleware._blocked(req)
    assert (denied is None) == (enabled is True)
    if denied is not None:
        assert 'datalake_access_required' in denied.update['messages'][0].content
    req.state['messages'].append(AIMessage('', tool_calls=[{'id': 'chosen', 'name': tool, 'args': {'query': text}}]))
    req.tool_call['name'] = 'web_search'
    assert 'datalake_external_fallback_forbidden' in middleware._blocked(req).update['messages'][0].content
    req.tool_call['name'] = 'process_local_data'
    assert middleware._blocked(req) is None


@pytest.mark.parametrize('enabled', [True, False])
def test_model_actually_sees_the_two_tools_only_when_authorized(enabled):
    req = request(QUERIES[0], enabled)
    req.system_message = SystemMessage('Original instruction')
    req.tools = [{'name': n} for n in ['datalake_data_qa', 'datalake_smart_qa', 'web_search', 'read_file']]
    req.override = lambda **kwargs: SimpleNamespace(**kwargs)
    result = DataLakeSourceMiddleware()._prepare(req)
    names = {t['name'] for t in result.tools}
    assert names == ({'datalake_data_qa', 'datalake_smart_qa', 'read_file', 'web_search'} if enabled else {'read_file', 'web_search'})
    assert '天津公司有多少气田' in str(result.system_message.content)


def test_model_selects_authorized_tool_without_a_second_intent_classifier():
    req = request('写一篇气田开发论文', True)
    req.tool_call['args']['query'] = QUERIES[0]
    assert DataLakeSourceMiddleware()._blocked(req) is None
    assert not query_scope(req.state)['required']


def test_attachment_text_cannot_supply_intent():
    message = HumanMessage('帮我写论文\n' + QUERIES[0], additional_kwargs={'original_user_content': '帮我写论文'})
    assert not query_scope({'messages': [message]})['required']


def test_followup_is_a_new_model_decision_with_tools_still_available():
    state = {'messages': [HumanMessage(QUERIES[0]), AIMessage('', tool_calls=[{'id': 't', 'name': 'datalake_data_qa', 'args': {}}]),
                          ToolMessage('synthetic result', name='datalake_data_qa', tool_call_id='t')]}
    scope = query_scope(state, {'run_id': 'one'})
    assert scope['phase'] == 'processing'
    for text in ['根据刚才的结果生成Excel', '那北京呢', '换个任务，帮我写一篇论文']:
        following = query_scope({STATE_KEY: scope, 'messages': [*state['messages'], HumanMessage(text)]}, {'run_id': 'two'})
        assert not following['required']
        assert {'datalake_data_qa', 'datalake_smart_qa'} <= visible(text)


@pytest.mark.parametrize('async_mode', [False, True])
@pytest.mark.asyncio
async def test_real_graph_can_execute_natural_question_tool_call(async_mode):
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from test_staged_execution import ScriptedModel, call
    queries = []
    @tool
    def datalake_data_qa(query: str) -> str:
        """Return synthetic main-data evidence."""
        queries.append(query)
        return 'synthetic fixture; not real enterprise data'
    model = ScriptedModel(responses=[call('datalake_data_qa', 't', query=QUERIES[0]), AIMessage('Synthetic answer')])
    graph = create_agent(model, tools=[datalake_data_qa], context_schema=dict, middleware=[DataLakeSourceMiddleware()])
    inputs = {'messages': [HumanMessage(QUERIES[0])]}
    kwargs = {'context': {'run_id': 'synthetic', 'datalake_enabled': True}}
    result = await graph.ainvoke(inputs, **kwargs) if async_mode else graph.invoke(inputs, **kwargs)
    assert queries == [QUERIES[0]]
    assert result[STATE_KEY]['phase'] == 'processing'
    assert not any(m.status == 'error' for m in result['messages'] if isinstance(m, ToolMessage))


def visible(text):
    req = request(text, True)
    req.system_message = SystemMessage('Original instruction')
    req.tools = [{'name': n} for n in ['datalake_data_qa', 'datalake_smart_qa', 'bash']]
    req.override = lambda **kwargs: SimpleNamespace(**kwargs)
    return {t['name'] for t in DataLakeSourceMiddleware()._prepare(req).tools}
