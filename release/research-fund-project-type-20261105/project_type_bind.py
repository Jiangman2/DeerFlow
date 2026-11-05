"""Offline successor packaging; binds the accepted Round3 receipt and rechecks live state on execution."""
import argparse,base64,copy,json
from pathlib import Path
import round3_release_builder as b
import round3_qualification_plan as q

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
NAME='update-portal-research-fund-project-type-20261105-r1.py'
BEFORE='0d935a5e5c45e8bf916b6b52ee310826d68acb22fffb0cbc70ab5d3304840a4c'
PARENT_REPORT_SHA='2804e8c39c133145f676b2cc502c651d392937443c207cb6307ac6d034a83fab'
ALLOWED=('SKILL.md','references/data-contract.md','references/group-company-standard-input.md','references/group-company-template-annotations.md','scripts/group_template.py','scripts/standard_input.py')

PARENT_VERIFIER='''
def verify_prior_release():
    """Read the immutable successful production receipt before claiming this operation."""
    path = Path(BUNDLE['prior_release']['path'])
    check(path.is_absolute() and all(not p.is_symlink() for p in (path,) + tuple(path.parents)), 'PRIOR_RECEIPT_SYMLINK')
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        check(stat.S_ISREG(before.st_mode) and before.st_size == BUNDLE['prior_release']['bytes'], 'PRIOR_RECEIPT_SIZE_CHANGED')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(BUNDLE['prior_release']['bytes'] + 1)
        after = os.fstat(fd)
        check((before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns), 'PRIOR_RECEIPT_CHANGED_DURING_READ')
    finally:
        os.close(fd)
    check(len(raw) == BUNDLE['prior_release']['bytes'] and sha(raw) == BUNDLE['prior_release']['sha256'], 'PRIOR_RECEIPT_DIGEST_CHANGED')
    receipt = json.loads(raw)
    check(receipt.get('status') == 'RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED' and receipt.get('exitcode') == 0 and receipt.get('maintenance_pod_absent') is True and receipt.get('runtime_node_views_accepted') is True, 'PRIOR_RELEASE_NOT_ACCEPTED')
    check(receipt.get('after_tree_sha256') == BUNDLE['before']['tree_sha256'], 'PRIOR_RELEASE_TREE_CHANGED')

'''

def prepare(parent,qa,approved_tree):
    parent_source=b.read_regular(parent/'update-portal-research-fund-skill-round3-20261105-r1.py').decode()
    old=b.literal_binding(parent_source,'BUNDLE')
    receipt_raw=b.read_regular(parent/'operator-update-20261105.json')
    receipt=json.loads(receipt_raw)
    b.require(receipt['status']=='RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED' and receipt['exitcode']==0 and receipt['runtime_node_views_accepted'] and receipt['maintenance_pod_absent'],'PARENT_RELEASE_NOT_ACCEPTED')
    b.require(receipt['after_tree_sha256']==old['after']['tree_sha256']==BEFORE and receipt['report_sha256']==PARENT_REPORT_SHA and receipt['report_bytes']==7258,'PARENT_RELEASE_BINDING_CHANGED')
    b.require(receipt['checked_deployments_unchanged']==sorted(old['deployments']) and not receipt['workload_patch_requests'] and not receipt['production_database_access'],'PARENT_PRODUCTION_SCOPE_CHANGED')
    b.require(sorted(r['node'] for r in receipt['after_consumers'])==old['gateway_nodes'] and all(r['status']=='PASS' and r['tree_sha256']==BEFORE for r in receipt['after_consumers']),'PARENT_NODE_COVERAGE_CHANGED')
    after=b.manifest(ROOT/'skills/public/research-fund-settlement')
    b.require(after==json.loads((HERE/'candidate-manifest.json').read_bytes()) and after['tree_sha256']==approved_tree,'CANDIDATE_CHANGED')
    before=old['after']
    oldfiles={r['path']:r for r in before['files']}; newfiles={r['path']:r for r in after['files']}
    b.require(set(oldfiles)==set(newfiles) and sorted(p for p in oldfiles if oldfiles[p]!=newfiles[p])==sorted(ALLOWED),'SCOPE_EXCEEDS_PROJECT_TYPE')
    qualifier=b.read_regular(qa/'project_type_qualifier.py').decode()
    plan=json.loads((HERE/'qualification-plan.json').read_bytes())
    qualified=json.loads((qa/'qualification-run.json').read_bytes())
    qualified.update(schema='research-fund-round3-synthetic-qualification.v1',qualifier_sha256=b.sha(qualifier.encode()))
    q.validate_receipt(plan,qualified,qualifier,approved_tree,ROOT)
    bundle=copy.deepcopy(old)
    bundle.update(schema='research-fund-project-type-sealed-bundle.v1',before=before,after=after,
        approved_candidate_tree_sha256=approved_tree,baseline_tool_revision='round3-accepted-20261105',
        baseline_status='RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED',baseline_receipt_sha256=PARENT_REPORT_SHA,
        prior_release={'path':receipt['report_path'],'bytes':7258,'sha256':PARENT_REPORT_SHA},
        parent_operator_capture_sha256=b.sha(receipt_raw),
        qualification_plan_sha256=b.sha(b.canonical(plan)),qualification_receipt_sha256=b.sha(b.canonical(qualified)))
    # Keep the already-reviewed runtime source readers; change their expected Skill manifest only.
    bundle['inspector_source']=b.replace_binding(old['inspector_source'],'EXPECTED_SKILL',before)
    bundle['readonly_child']=b.literal_binding(bundle['inspector_source'],'CHILD')
    b.require(bundle['readonly_child']==old['readonly_child'],'READONLY_READER_CHANGED')
    archive=b.archive_for(ROOT/'skills/public/research-fund-settlement',after)
    bundle.update(archive_b64=base64.b64encode(archive).decode(),archive_sha256=b.sha(archive))
    remote=(parent/'round3_remote_template.py').read_text(encoding='utf-8').replace('round3-20261105-r1','project-type-20261105-r1').replace("'round3-feedback'","'project-type-feedback'")
    remote=b.replace_binding(remote,'QUALIFIER_SOURCE',qualifier)
    remote=b.replace_binding(remote,'QUALIFIER_EXPECTED',qualified['passed'])
    bundle['remote_source']=remote
    for key in ('inspector_source','remote_source','readonly_child'):
        bundle[key+'_sha256']=b.sha(bundle[key].encode())
    template=(parent/'round3_updater_template.py').read_text(encoding='utf-8')
    replacements={
        'Unbound Round3 Skill updater template. Await fresh r3 receipt and approved candidate.':'One-Skill project-type update. Requires accepted Round3 receipt and fresh unchanged runtime checks.',
        'import socket\n':'import socket\nimport stat\n',
        'round3-20261105-r1':'project-type-20261105-r1',
        'research-fund-round3-sealed-bundle.v1':'research-fund-project-type-sealed-bundle.v1',
        "BUNDLE.get('baseline_tool_revision') == '20261105-r3' and BUNDLE.get('baseline_status') == 'READONLY_FINANCE_SKILL_BASELINE_ACCEPTED_NO_CHANGE', 'FRESH_ROUND3_BASELINE_REQUIRED'":"BUNDLE.get('baseline_tool_revision') == 'round3-accepted-20261105' and BUNDLE.get('baseline_status') == 'RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED', 'ACCEPTED_ROUND3_RELEASE_REQUIRED'",
        '6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951':BEFORE,
        'def main():':PARENT_VERIFIER+'def main():',
        "check(os.geteuid() == 0, 'ROOT_REQUIRED')":"check(os.geteuid() == 0, 'ROOT_REQUIRED')\n        verify_prior_release()",
    }
    for oldtext,newtext in replacements.items():
        b.require(oldtext in template,'TEMPLATE_ADAPTATION_TARGET_CHANGED')
        template=template.replace(oldtext,newtext)
    updater=b.replace_binding(template,'BUNDLE',bundle)
    return {'bundle':bundle,'updater':updater,'template':template,'remote':remote,'qualified':qualified}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent',type=Path,default=HERE.parent/'research-fund-feedback-round3-20261105')
    parser.add_argument('--qa',type=Path,required=True)
    parser.add_argument('--approved-tree',required=True)
    args=parser.parse_args()
    result=prepare(args.parent,args.qa,args.approved_tree)
    outputs={NAME:result['updater'],'project_type_updater_template.py':result['template'],
        'project_type_remote.py':result['remote'],'project_type_qualifier.py':(args.qa/'project_type_qualifier.py').read_text(encoding='utf-8'),
        'qualification-receipt.json':json.dumps(result['qualified'],sort_keys=True,indent=2)+'\n'}
    for name,text in outputs.items():
        (HERE/name).write_bytes(text.encode())
    provenance={'scope':'project_type_text_only','before_tree':BEFORE,'after_tree':args.approved_tree,
        'changed_skill_files':list(ALLOWED),'parent_updater_sha256':b.sha((args.parent/'update-portal-research-fund-skill-round3-20261105-r1.py').read_bytes()),
        'parent_operator_capture_sha256':result['bundle']['parent_operator_capture_sha256'],
        'production_updated':False,'fresh_live_checks_required':True}
    (HERE/'build-provenance.json').write_text(json.dumps(provenance,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'SEALED_LOCAL_CANDIDATE_NOT_DEPLOYED','file':NAME,'bytes':len(result['updater'].encode()),'sha256':b.sha(result['updater'].encode()),'regressions':result['qualified']['passed']}))

if __name__=='__main__': main()
