"""Offline tests of the real scheduler formatter and sender closure; no sends."""
import ast
import datetime
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urlsplit
import unittest
from zoneinfo import ZoneInfo

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
SOURCE=HERE/'backend/app/scheduler'
THREAD='11111111-2222-4333-8444-555555555555'
ORIGIN='https://cnoocclaw.cnooc.com.cn'
READER=ORIGIN+'/reports/energy-daily/'+THREAD+'/2026-11-15.html'
CHAT=ORIGIN+'/workspace/chats/'+THREAD


def validate_thread(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',value):raise ValueError()
    return value


def functions(filename,names):
    source=(SOURCE/filename).read_text(encoding='utf-8')
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in names]
    scope={'re':re,'urlsplit':urlsplit,'datetime':datetime.datetime,'date':datetime.date,'UTC':datetime.timezone.utc,
           'ZoneInfo':ZoneInfo,'validate_thread_id':validate_thread,'WorkNotification':SimpleNamespace}
    tree=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[]))
    exec(compile(tree,'<actual-work-functions>','exec'),scope)
    return scope


def finish_coroutine(coro):
    # The fake sender completes synchronously. No event loop or socketpair.
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()
    raise AssertionError('Unexpected asynchronous transport in isolated test')


class NotificationLinkTests(unittest.TestCase):
    def setUp(self):
        self.format=functions('work_notification_delivery.py',{'_message','_daily_reader_url'})['_message']
        self.summary=functions('work_notifications.py',{'_notification_summary'})['_notification_summary']

    def notification(self,text,thread=THREAD):
        return SimpleNamespace(title='能源日报',result_text=self.summary(text),thread_id=thread,finished_at='2026-11-15T00:07:00+00:00',
                               owner_user_id='owner-fixture',connection_id='connection-fixture',delivery_id='delivery-fixture',binding_key='binding-fixture')

    def render(self,text,thread=THREAD):
        return self.format(self.notification(text,thread),ORIGIN)

    def dispatcher(self):
        scope=functions('work_notification_delivery.py',{'_message','_daily_reader_url','build_work_notification_dispatcher'})
        calls=[]
        async def sender(*args,**kwargs):calls.append((args,kwargs))
        scope.update(work_notifications_enabled=lambda:True,get_session_factory=lambda:'sessions-fixture',
                     WorkSettings=SimpleNamespace(from_env=lambda:SimpleNamespace(profile='production')),
                     _platform_origin=lambda profile:ORIGIN,send_work_notification=sender,
                     WorkNotificationError=type('SyntheticWorkError',(Exception,),{}),
                     WorkNotificationDispatcher=lambda sessions,**kwargs:SimpleNamespace(sessions=sessions,**kwargs),
                     os=SimpleNamespace(getenv=lambda key,default='':'state-key-fixture' if key=='DEER_FLOW_WORK_STATE_KEY' else default))
        return scope['build_work_notification_dispatcher'](),calls

    def test_final_daily_is_exactly_one_markdown_link(self):
        text='油气与金融市场日报\n发布日期：2026-11-15；行情日期：2026-11-14\n正文\n\n[点击查看日报网页]('+READER+')\n\n[点击查看完整日报]('+CHAT+')'
        self.assertEqual(self.render(text),'[点击查看日报网页]('+READER+')')

    def test_long_daily_keeps_only_reader_without_report_body(self):
        text='油气与金融市场日报\n'+'已核实内容。\n'*1800+'\n[点击查看日报网页]('+READER+')\n\n[点击查看完整日报]('+CHAT+')'
        clipped=self.summary(text)
        self.assertLess(len(clipped.encode()),2048)
        self.assertNotIn('已核实内容',clipped)
        self.assertEqual(self.render(text),'[点击查看日报网页]('+READER+')')

    def test_other_threads_hosts_signed_urls_and_invalid_dates_not_sent(self):
        bad=[READER.replace(THREAD,'another-thread'),READER.replace('cnoocclaw.cnooc.com.cn','evil.invalid'),READER+'?token=PRIVATE',
             READER+'#fragment',READER.replace('https://','http://'),READER.replace('2026-11-15','2026-02-30'),
             READER.replace('https://','https://user:PRIVATE@')]
        for url in bad:
            with self.subTest(url=url):self.assertEqual(self.render('油气与金融市场日报\n[点击查看日报网页]('+url+')'),'')

    def test_daily_news_and_status_are_not_in_notification(self):
        text='油气与金融市场日报\n数据不完整\n[新闻](https://oilprice.com/Energy/Crude-Oil/news.html)\nhttps://example.invalid/private?token=PRIVATE\n[点击查看日报网页]('+READER+')'
        self.assertEqual(self.render(text),'[点击查看日报网页]('+READER+')')

    def test_non_daily_message_keeps_existing_bound_and_platform_chat_link(self):
        text='普通任务结果'+('a'*2000)+'\nhttps://example.invalid/private?token=PRIVATE'
        self.assertEqual(self.summary(text),text[:1200])
        value=self.render(text)
        self.assertIn('【定时任务】能源日报已完成',value)
        self.assertIn('完成时间：2026-11-15 08:07（北京时间）',value)
        self.assertIn('查看完整结果：'+CHAT,value)
        self.assertNotIn(READER,value)

    def test_approved_platform_alias_is_canonicalized_to_operator_origin(self):
        alias='https://heai.cnooc.com.cn'
        value=self.format(self.notification('油气与金融市场日报\n[点击查看日报网页]('+READER+')'),alias)
        self.assertEqual(value,'[点击查看日报网页]('+READER.replace(ORIGIN,alias)+')')

    def test_two_conflicting_report_dates_are_not_silently_selected(self):
        other=READER.replace('2026-11-15','2026-11-14')
        self.assertEqual(self.render('油气与金融市场日报\n[点击查看日报网页]('+READER+')\n[点击查看日报网页]('+other+')'),'')

    def test_invalid_task_identity_never_preserves_a_reader_link(self):
        self.assertEqual(self.render('油气与金融市场日报\n[点击查看日报网页]('+READER+')',thread='../other'),'')

    def test_missing_reader_is_not_a_completion_or_conversation_fallback(self):
        self.assertEqual(self.render('油气与金融市场日报\n日报文件已生成\n[点击查看完整日报]('+CHAT+')'),'')

    def test_preface_and_duplicate_same_reader_still_produce_one_link(self):
        text='本期日报如下\n油气与金融市场日报\n[点击查看日报网页]('+READER+')\n[点击查看日报网页]('+READER+')'
        self.assertEqual(self.render(text),'[点击查看日报网页]('+READER+')')

    def test_missing_reader_fails_without_calling_sender(self):
        dispatcher,calls=self.dispatcher()
        self.assertEqual(finish_coroutine(dispatcher.send(self.notification('油气与金融市场日报\n没有已确认的网页链接'))),'failed')
        self.assertEqual(calls,[])

    def test_valid_reader_calls_sender_once_with_exact_link_and_original_binding(self):
        dispatcher,calls=self.dispatcher()
        notification=self.notification('油气与金融市场日报\n正文\n[点击查看日报网页]('+READER+')')
        self.assertEqual(finish_coroutine(dispatcher.send(notification)),'sent')
        self.assertEqual(calls,[(('owner-fixture','connection-fixture','[点击查看日报网页]('+READER+')','delivery-fixture'),
                                 {'binding_key':'binding-fixture','markdown':True})])
        self.assertEqual(dispatcher.sessions,'sessions-fixture')
        self.assertEqual(dispatcher.key,'state-key-fixture')


if __name__=='__main__':unittest.main()
