"""Derive a new release tool from the accepted Skill-only publication workflow."""
import ast
import hashlib
import json
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
OLD=REPO/'portable-skills/deployment/html-warning-skill-20261114-r1'
NEW=REPO/'portable-skills/deployment/html-reader-skill-20261114-r1'
SKILL=REPO/'portable-skills/candidates/energy-market-daily-2.1.5'


def main():
    assert not NEW.exists(),'NEW_TOOL_ALREADY_EXISTS'
    NEW.mkdir()
    for name in ['operator.py','publisher.py','verify_child.py','compose_probe.py','seal.py']:
        source=(OLD/name).read_text(encoding='utf-8')
        source=source.replace('energy-market-daily-2.1.4','energy-market-daily-2.1.5').replace('2.1.4-portable','2.1.5-portable')
        source=source.replace('energy-daily-html-warning','energy-daily-html-reader-skill').replace('energy-html-warning','energy-html-reader-skill')
        source=source.replace('html-warning-skill','html-reader-skill')
        source=source.replace('test_energy_html_delivery_20261114.py','test_energy_reader_delivery_20261114.py').replace('test_energy_html_news_stage_20261114.py','test_energy_reader_news_stage_20261114.py')
        source=source.replace('a7b41e944717a2fa567455a3b98e1e36e09920c1c0d6afc4fe786d9452b01a5d','2a09015d31cee41cd2a329fff96062848255d3d49fb7cec8c52d47c939345259')
        if name=='operator.py':
            source=source.replace("OLD_INSTALL = Path('/var/lib/portal-energy-programmatic-skill-install-20261114-r1/REPORT.json')", "OLD_INSTALL = Path('/var/lib/portal-energy-html-warning-skill-install-20261114-r1/REPORT.json')")
            source=source.replace('3d16bea239ecde303aa32399e3a9d1c839bbb49a3f98ce4327ef5cc143b46d42','5dd40639193ff9a9776c235b084977d47219ea2fde4ff154822feca625066663')
            source=source.replace('OLD_INSTALL_BYTES = 9093','OLD_INSTALL_BYTES = 9328').replace('af7750a214e54d2ca4e951667e514e02','91ae0c5e6aff42dbb0b73aef146732d0')
            source=source.replace("fixed=[['get','deployments']", "fixed=[['get','deployment','nginx','-o','json'],['get','deployments']")
            marker='    def run(self):\n'
            assert source.count(marker)==1
            method='''    def reader_binding(self):
        raw=read(Path('/var/lib/portal-energy-html-reader-20261114-r1/RUNTIME-ACCEPTANCE.json'))
        receipt=json.loads(raw)
        need(receipt.get('schema')=='energy-html-reader-activation.v1'
             and receipt.get('status')=='AUTHENTICATED_HTML_READER_DEPLOYED'
             and receipt.get('anonymous_login_redirect_verified') is True
             and receipt.get('reader_configmap')=='deer-flow-nginx-energy-reader-20261114-r1',
             'AUTHENTICATED_HTML_READER_ACCEPTANCE_REQUIRED')
        current=self.command(['get','deployment','nginx','-o','json'])
        actual={'uid':current['metadata']['uid'],'generation':current['metadata']['generation'],'spec_sha256':sha(canonical(current['spec']))}
        need(actual==receipt.get('nginx_after') and actual['uid']=='10fb043a-eaee-433e-bce0-80688e798611',
             'AUTHENTICATED_HTML_READER_BASELINE_CHANGED')
        self.out['accepted_html_reader']={'receipt_sha256':sha(raw),'nginx':actual,
                                         'anonymous_login_redirect_verified':True}

'''
            source=source.replace(marker,method+marker+'        self.reader_binding()\n')
            source=source.replace("print(json.dumps({'phase':'PUBLISH_ONLY_QUALIFIED_ENERGY_SKILL'}),flush=True)",
                                  "self.reader_binding()\n        print(json.dumps({'phase':'PUBLISH_ONLY_QUALIFIED_ENERGY_SKILL'}),flush=True)")
        if name=='seal.py':
            source=source.replace("['ProgrammaticBuildTests', 'BrowserPipelineTests']", "['ProgrammaticBuildTests', 'BrowserPipelineTests', 'HtmlLinkTests']")
            source=source.replace('OFFLINE_TEST_COUNT = 27','OFFLINE_TEST_COUNT = 34')
        if name=='compose_probe.py':
            marker="    require(text == daily.reply_text(data).rstrip() + '\\n\\n[点击查看完整日报](' + url + ')\\n'"
            assert source.count(marker)==1
            source=source.replace(marker,"    from delivery_text import html_report_url\n    reader=html_report_url({'conversation_url':url},data['publication_date'])\n    require(text == daily.reply_text(data).rstrip() + '\\n\\n[点击查看日报网页](' + reader + ')\\n\\n[点击查看完整日报](' + url + ')\\n'")
            source=source.replace("'single_text_ready_before_publication': True,", "'single_text_ready_before_publication': True,\n        'html_reader_link_in_same_text': True,")
        (NEW/name).write_text(source,encoding='utf-8')
        compile(source,str(NEW/name),'exec')
    tests=REPO/'portable-skills/tests'
    old=(tests/'test_energy_html_delivery_20261114.py').read_text(encoding='utf-8')
    old=old.replace("candidates/energy-market-daily-2.1.4'","candidates/energy-market-daily-2.1.5'")
    extra=(REPO/'portable-skills/deployment/html-reader-20261114-r1/test_skill.py').read_text(encoding='utf-8')
    extra=extra[extra.index('class HtmlLinkTests'):extra.index('    def test_business_modules_unchanged')]
    extra=extra.replace('URL','LINK_URL').replace('READER','LINK_READER').replace('DATA','LINK_DATA').replace('TEXT','LINK_TEXT')
    defs="\nLINK_URL='https://cnoocclaw.cnooc.com.cn/workspace/chats/11111111-2222-4333-8444-555555555555'\nLINK_READER='https://cnoocclaw.cnooc.com.cn/reports/energy-daily/11111111-2222-4333-8444-555555555555/2026-11-14.html'\nLINK_DATA={'publication_date':'2026-11-14','data_date':'2026-11-13','quality':'完整'}\nLINK_TEXT='油气与金融市场日报\\n发布日期：2026-11-14；行情日期：2026-11-13\\n报告状态：完整\\n'\n"
    # Put new tests before the standalone runner; no existing fixture is changed.
    marker="if __name__ == '__main__':"
    if marker in old:old=old[:old.index(marker)]
    (tests/'test_energy_reader_delivery_20261114.py').write_text(old+defs+extra+'\n',encoding='utf-8')
    news=(tests/'test_energy_html_news_stage_20261114.py').read_text(encoding='utf-8').replace("candidates/energy-market-daily-2.1.4'","candidates/energy-market-daily-2.1.5'")
    (tests/'test_energy_reader_news_stage_20261114.py').write_text(news,encoding='utf-8')
    preservation={'schema':'energy-core-preservation.v1','files':{}}
    for name in ['common.py','market.py','research.py','report_renderer.py','browser_runtime.py']:
        raw=(SKILL/'scripts'/name).read_bytes();prior=(SKILL.with_name('energy-market-daily-2.1.4')/'scripts'/name).read_bytes();assert raw==prior
        digest=hashlib.sha256(raw).hexdigest();preservation['files'][name]={'prior_sha256':digest,'candidate_sha256':digest}
    (NEW/'CORE-SOURCE-PRESERVATION.json').write_text(json.dumps(preservation,indent=2)+'\n',encoding='utf-8')
    print(str(NEW))


if __name__=='__main__':main()
