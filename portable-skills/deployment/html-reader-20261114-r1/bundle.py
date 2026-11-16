"""One operator command: activate the reader, then qualify/publish Skill 2.1.5."""
import base64
import hashlib
import json
import os
import sys
import types

ASSETS = {}


def main():
    out={'schema':'energy-html-reader-and-skill-release.v1','status':'INCOMPLETE','credentials_exported':False,
         'provider_requests':0,'work_messages_sent':0,'gateway_rollout_requested':False,'schedule_created':False}
    try:
        if sys.argv[1:] not in (['--install-daily-html-links-once'],['--verify-reader-readonly']):raise ValueError('EXPLICIT_MODE_REQUIRED')
        if os.name!='posix' or os.uname().nodename.split('.')[0]!='datax-01' or os.geteuid()!=0:raise ValueError('DATAX01_ROOT_REQUIRED')
        if set(ASSETS)!={'reader','skill'}:raise ValueError('SEALED_RELEASE_REQUIRED')
        modules={}
        for name,item in ASSETS.items():
            raw=base64.b64decode(item['base64'],validate=True)
            if len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('SEALED_RELEASE_INTEGRITY_FAILED')
            module=types.ModuleType('_energy_html_release_'+name);sys.modules[module.__name__]=module
            module.__file__=__file__;exec(compile(raw,'<sealed-energy-'+name+'>','exec'),module.__dict__);modules[name]=module
        readonly=sys.argv[1]=='--verify-reader-readonly'
        sys.argv=[__file__,'--verify-readonly' if readonly else '--activate-html-reader-once']
        if modules['reader'].main()!=0:
            out['error_code']='READER_STEP_INCOMPLETE_PRESERVE_OUTPUT_DO_NOT_REPLAY'
        elif readonly:out.update(status='READER_READONLY_CHECK_FINISHED',skill_update_requested=False)
        else:
            sys.argv=[__file__,'--install-html-reader-skill-once']
            if modules['skill'].main()!=0:out['error_code']='SKILL_STEP_INCOMPLETE_READER_MAY_BE_DEPLOYED_DO_NOT_REPLAY'
            else:
                out.update(status='HTML_READER_AND_SKILL_215_INSTALLED',skill_updated=True,old_skill_removed_from_discovery=True,
                           links_ready_in_same_final_text=True,business_acceptance_pending=True,
                           main_link_text='点击查看日报网页',secondary_link_text='点击查看完整日报',
                           access='Existing Haineng login and current conversation file permissions')
    except (Exception,KeyboardInterrupt) as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if type(exc) is ValueError else 'RELEASE_FAILED_REDACTED')
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())
