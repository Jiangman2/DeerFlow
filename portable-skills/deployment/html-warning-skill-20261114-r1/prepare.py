"""Prepare the narrow HTML display follow-up; never modify prior releases."""
import ast
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
NEW = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.4'
PRIOR = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.3'
TEMPLATES = HERE.parent / 'programmatic-skill-20261114-r1'
OLD_TREE = '915a4c765fc81e20b054bc77aa08ce9e919599d354a2aee7edc19db6637c1357'
TREE = 'a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d'
NEW_KEY = 'global_warning_hidden_in_html_and_png'
OLD_KEY = 'global_warning_hidden_in_png_only'


def write(path, text):
    assert not path.exists(), 'NEW_TARGET_EXISTS:' + str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def once(text, before, after):
    assert text.count(before) == 1, 'EXACT_SOURCE_MARKER:' + before[:100]
    return text.replace(before, after, 1)


def main():
    assert not NEW.exists(), 'CANDIDATE_ALREADY_EXISTS'
    shutil.copytree(PRIOR, NEW, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'MANIFEST.json', 'CANDIDATE.md'))
    daily = (NEW / 'scripts/daily.py').read_text(encoding='utf-8')
    daily = once(daily, "VERSION = '2.1.3-portable'", "VERSION = '2.1.4-portable'")
    daily = once(daily, "PROJECT_VERSIONS = frozenset((VERSION,", "PROJECT_VERSIONS = frozenset((VERSION, '2.1.3-portable',")
    daily = once(daily, 'from report_renderer import render\n', 'from report_renderer import render as render_template\n')
    marker = '\n\ndef require(ok, code, **details):'
    wrapper = '''\n\ndef render(data):
    """Hide only the fixed global warning in both delivered HTML and PNG."""
    html = render_template(data)
    if not data.get('formal_report_ready', True):
        prefix = '<aside style="background:#fff0df;'
        require(html.count(prefix) == 1, 'REPORT_GLOBAL_WARNING_IDENTITY_INVALID')
        html = html.replace(prefix, '<aside hidden style="display:none;background:#fff0df;', 1)
    return html
'''
    daily = once(daily, marker, wrapper + marker)
    daily = daily.replace(OLD_KEY, NEW_KEY)
    daily = daily.replace('# Only the fixed renderer\'s exact global warning is hidden in PNG. The\n    # source HTML, JSON/TXT quality, per-quote marks and reference dates stay intact.',
                          '# HTML already hides this exact global warning. Screenshot validation\n    # preserves that choice; JSON/TXT quality and per-quote dates stay intact.')
    daily = once(daily, "if receipt.get('version') in (VERSION, '2.1.2-portable'):",
                 "if receipt.get('version') in (VERSION, '2.1.3-portable', '2.1.2-portable'):")
    daily = once(daily, "            if receipt['version'] == VERSION:\n", "            if receipt['version'] in (VERSION, '2.1.3-portable'):\n")
    old = "        if receipt['version'] == VERSION:\n            require(receipt['png']['programmatic_checks'].get('" + NEW_KEY + "')\n                    is (not data['formal_report_ready']), 'RESULT_SCREENSHOT_MODE_MISMATCH')"
    new = old + "\n        elif receipt['version'] == '2.1.3-portable':\n            require(receipt['png']['programmatic_checks'].get('" + OLD_KEY + "')\n                    is (not data['formal_report_ready']), 'RESULT_SCREENSHOT_MODE_MISMATCH')"
    daily = once(daily, old, new)
    (NEW / 'scripts/daily.py').write_text(daily, encoding='utf-8', newline='\n')
    delivery = (NEW / 'scripts/delivery_text.py').read_text(encoding='utf-8')
    delivery = once(delivery, "receipt.get('version') == '2.1.3-portable'", "receipt.get('version') in ('2.1.3-portable', '2.1.4-portable')")
    (NEW / 'scripts/delivery_text.py').write_text(delivery, encoding='utf-8', newline='\n')
    skill = (NEW / 'SKILL.md').read_text(encoding='utf-8')
    skill = skill.replace('PNG 不显示顶部整块全局“数据不完整”提示框', 'HTML 和 PNG 均不显示顶部整块全局“数据不完整”提示框')
    skill = skill.replace('HTML、JSON、完整 TXT 继续如实保留报告完整性状态；隐藏 PNG 的提示框不代表数据变完整。',
                          'JSON、完整 TXT 继续如实保留报告完整性状态；HTML 和 PNG 的逐项日期、缺值及休市标记继续保留。')
    (NEW / 'SKILL.md').write_text(skill, encoding='utf-8', newline='\n')

    # Reuse current acceptance cases, keeping their immutable 2.1.2 fixtures.
    tests = ROOT / 'portable-skills/tests'
    for old_name, new_name in [('test_energy_programmatic_delivery_20261114.py', 'test_energy_html_delivery_20261114.py'),
                               ('test_energy_news_stage_20261114.py', 'test_energy_html_news_stage_20261114.py')]:
        code = (tests / old_name).read_text(encoding='utf-8').replace('energy-market-daily-2.1.3', 'energy-market-daily-2.1.4').replace(OLD_KEY, NEW_KEY)
        if old_name.startswith('test_energy_programmatic'):
            code = code.replace('test_only_identified_global_warning_is_hidden_and_html_unchanged', 'test_warning_is_hidden_in_delivered_html_and_png')
            code = once(code, "        self.assertIn('数据不完整 · 本期含取数缺口',before.decode())",
                        "        self.assertIn('<aside hidden style=\"display:none;',before.decode())\n        self.assertIn('数据不完整 · 本期含取数缺口',before.decode())")
        write(tests / new_name, code)

    for filename in ('operator.py', 'publisher.py', 'verify_child.py', 'compose_probe.py', 'seal.py', 'test_api.py'):
        code = (TEMPLATES / filename).read_text(encoding='utf-8')
        code = code.replace('energy-market-daily-2.1.3', 'energy-market-daily-2.1.4').replace('2.1.3-portable', '2.1.4-portable')
        code = code.replace(OLD_TREE, TREE).replace(OLD_KEY, NEW_KEY)
        code = code.replace('test_energy_programmatic_delivery_20261114.py', 'test_energy_html_delivery_20261114.py')
        code = code.replace('test_energy_news_stage_20261114.py', 'test_energy_html_news_stage_20261114.py')
        code = code.replace('energy-daily-programmatic-20261114-r1', 'energy-daily-html-warning-20261114-r1')
        code = code.replace('install-energy-programmatic-skill-20261114-r1.py', 'install-energy-html-warning-skill-20261114-r1.py')
        code = code.replace('--install-programmatic-skill-once', '--install-html-warning-skill-once')
        code = code.replace('portal-energy-programmatic-skill-install-20261114-r1', 'portal-energy-html-warning-skill-install-20261114-r1')
        code = code.replace('portal.energy-programmatic-skill-install/operation', 'portal.energy-html-warning-skill-install/operation')
        if filename == 'operator.py':
            code = code.replace('/var/lib/portal-energy-delivery-context-skill-install-20261114-r1/REPORT.json', '/var/lib/portal-energy-programmatic-skill-install-20261114-r1/REPORT.json')
            code = code.replace('0da0874fec0386d3d0fd5bb33afa614fff97c3b059452ef16465abe981bfba88', '3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42')
            code = code.replace('OLD_INSTALL_BYTES = 8446', 'OLD_INSTALL_BYTES = 9093').replace('1ba3b07ffe08419d878e1f3460351c9d', 'af7750a214e54d2ca4e951667e514e02')
            code = code.replace("OLD_INSTALL_STATUS = 'ENERGY_DELIVERY_SKILL_INSTALLED'", "OLD_INSTALL_STATUS = 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'")
            # Structured rows tolerate Pending Pods with no imageID. Do not
            # abort a successful publication on a display-column shape.
            start = code.index('ORDINARY_COLUMNS = ')
            end = code.index('VISIBILITY_CHILD = ', start)
            code = code[:start] + "ORDINARY_LIST = ['get','pods','-l','app.kubernetes.io/component=sandbox','-o','json']\n" + code[end:]
            start = code.index('        raw=self.command(ORDINARY_LIST)', code.index('    def ordinary_visibility'))
            end = code.index('        if not rows:', start)
            replacement = """        listing=self.command(ORDINARY_LIST)
        need(isinstance(listing,dict) and isinstance(listing.get('items'),list),'ORDINARY_POD_LIST_SHAPE')
        rows=[]
        for pod in listing['items']:
            meta,status=pod.get('metadata',{}),pod.get('status',{})
            states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
            if status.get('phase')!='Running' or meta.get('deletionTimestamp') or len(states)!=1 or states[0].get('ready') is not True or states[0].get('imageID')!=IMAGE_ID:
                continue
            name,uid=meta.get('name',''),meta.get('uid','')
            need(re.fullmatch('sandbox-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'ORDINARY_POD_IDENTITY')
            rows.append({'pod':name,'uid':uid,'node':pod.get('spec',{}).get('nodeName')})
"""
            code = code[:start] + replacement + code[end:]
        if filename == 'compose_probe.py':
            code = once(code, "    require('数据不完整 · 本期含取数缺口' in paths['.html'].read_text(encoding='utf-8')",
                        "    require('<aside hidden style=\"display:none;' in paths['.html'].read_text(encoding='utf-8')")
            code = code.replace('FREIGHT_FACTS_MUST_REMAIN_IN_NON_PNG_ARTIFACTS', 'FREIGHT_FACTS_AND_HTML_DISPLAY_REQUIRED')
        write(HERE / filename, code)
    write(NEW / 'CANDIDATE.md', '# 2.1.4 本地候选\n\n仅扩展顶部提示框的隐藏范围到 HTML，保留逐项参考日期与 JSON/TXT 状态。已发布的 2.1.3 及历史报告保持独立。正式封包时生成新清单。\n')
    print(json.dumps({'candidate': str(NEW), 'status': 'PREPARED_NOT_SEALED'}))


if __name__ == '__main__':
    main()
