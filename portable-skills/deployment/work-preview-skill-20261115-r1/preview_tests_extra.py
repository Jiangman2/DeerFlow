class WorkPreviewTests(unittest.TestCase):
    setUp=ProgrammaticBuildTests.setUp
    tearDown=ProgrammaticBuildTests.tearDown

    def formatter(self, filename, names):
        import ast
        from urllib.parse import urlsplit
        from zoneinfo import ZoneInfo
        import datetime
        source=(Path(__file__).parent/'gateway-418'/filename).read_text(encoding='utf-8')
        selected=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
        scope={'re':__import__('re'),'urlsplit':urlsplit,'date':datetime.date,'datetime':datetime.datetime,'UTC':datetime.timezone.utc,
               'ZoneInfo':ZoneInfo,'validate_thread_id':lambda value:value}
        tree=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+selected,type_ignores=[]))
        exec(compile(tree,'<actual-gateway-418-formatters>','exec'),scope)
        return scope

    def notification(self,text):
        summary=self.formatter('work_notifications.py',{'_notification_summary'})['_notification_summary'](text)
        formatter=self.formatter('work_notification_delivery.py',{'_daily_reader_url','_message'})['_message']
        return formatter(types.SimpleNamespace(result_text=summary,thread_id=URL.rsplit('/',1)[1],title='日报',finished_at=''),'https://cnoocclaw.cnooc.com.cn')

    def test_build_text_keeps_its_reader_in_first_preview(self):
        report=daily.build(self.args)
        text=Path(report['final_text_file']).read_text(encoding='utf-8')
        expected='[点击查看日报网页]('+report['html_report_url']+')'
        self.assertEqual(text.splitlines()[0],expected)
        self.assertEqual(self.notification(text[:2000]),expected)
        self.assertEqual(self.notification(text[:2000][:2000]),expected)
        self.assertIn('油气与金融市场日报',text)

    def test_long_report_and_first_two_thousand_characters_yield_same_link(self):
        data=daily.snapshot(self.project)
        body=daily.reply_text(data)+'\n'+'已核实的新闻事实。'*900
        text=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True,link_first=True)
        self.assertGreater(len(text),2000)
        expected='[点击查看日报网页]('+delivery.html_report_url({'conversation_url':URL},data['publication_date'])+')'
        self.assertEqual(self.notification(text),expected)
        self.assertEqual(self.notification(text[:2000]),expected)
        self.assertIn(body.rstrip(),text)

    def test_current_final_text_compatibility_is_idempotent(self):
        report=daily.build(self.args);before={item['path']:Path(item['path']).read_bytes() for item in report['files']}
        result=daily.final_text(self.args)
        self.assertEqual(result['status'],'FINAL_TEXT_READY')
        text=Path(result['final_text_file']).read_text(encoding='utf-8')
        self.assertEqual(text.count('[点击查看日报网页]('),1)
        self.assertTrue(text.startswith('[点击查看日报网页]('))
        self.assertEqual(before,{path:Path(path).read_bytes() for path in before})

    def test_legacy_html_footer_format_remains_supported(self):
        data=daily.snapshot(self.project);body=daily.reply_text(data)
        old=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True)
        self.assertTrue(old.startswith('油气与金融市场日报'))
        self.assertEqual(delivery.report_text(data,old,{'conversation_url':URL},include_html_link=True),old)

    def test_other_conversation_header_is_rejected(self):
        data=daily.snapshot(self.project);body=daily.reply_text(data)
        text=delivery.report_text(data,body,{'conversation_url':URL},include_html_link=True,link_first=True)
        with self.assertRaises(Failure):delivery.report_text(data,text,{'conversation_url':OTHER},include_html_link=True,link_first=True)
