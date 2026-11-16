"""Verify 2.1.7/2.1.8 same text and the retained personal scheduler formatter."""
import ast
from datetime import date, datetime, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'portable-skills/candidates/energy-market-daily-2.1.8/scripts'))
path = ROOT / 'portable-skills/tests/test_energy_publication_owner_20261116.py'
spec = importlib.util.spec_from_file_location('delivery_comparison_fixture', path)
fx = importlib.util.module_from_spec(spec);spec.loader.exec_module(fx)
path = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.7/scripts/delivery_text.py'
spec = importlib.util.spec_from_file_location('prior_delivery', path)
previous = importlib.util.module_from_spec(spec);spec.loader.exec_module(previous)
with tempfile.TemporaryDirectory() as location:
    project = Path(location);fx.fx.fixtures.project_fixture(project)
    data = fx.daily.snapshot(project);body = fx.daily.reply_text(data);context = {'conversation_url': fx.fx.URL}
    old = previous.report_text(data, body, context, include_html_link=True, link_first=True)
    current = fx.daily.report_text(data, body, context, include_html_link=True, link_first=True)
    assert old == current
    namespace = {'re': re, 'urlsplit': urlsplit, 'date': date, 'datetime': datetime,
                 'UTC': timezone.utc, 'ZoneInfo': ZoneInfo, 'validate_thread_id': lambda value:value,
                 'WorkNotification': SimpleNamespace}
    source = (ROOT / 'portable-skills/deployment/energy-reader-delivery-repair-20261115-r2/backend/app/scheduler/work_notification_delivery.py').read_text(encoding='utf-8')
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in ('_message', '_daily_reader_url')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<retained-native-notification-formatter>', 'exec'), namespace)
    notification = SimpleNamespace(thread_id=fx.fx.URL.rsplit('/', 1)[1], result_text=current,
                                   title='油气与金融市场日报', finished_at=None)
    message = namespace['_message'](notification, 'https://cnoocclaw.cnooc.com.cn')
    assert message == current.splitlines()[0] and '\n' not in message
    print(json.dumps({'report_text_217_equals_218': True, 'native_scheduled_personal_notification_is_one_reader_link': True,
                      'notification_bytes': len(message.encode()), 'messages_sent': 0,
                      'actual_user_notification_not_yet_inspected': True}))
