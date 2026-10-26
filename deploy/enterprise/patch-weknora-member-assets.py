"""Reproduce the reviewed member UI source fix on the exact deployed Vite assets.

Output is candidate-only. It does not contact Kubernetes or change a live tree.
The install shell publishes a new module graph after the existing Office patch.
"""
import argparse
import hashlib
import json
from pathlib import Path

COMPONENT = 'OrganizationList-A-11TnnH.legacy-office-r6.js'
STORE = 'settings-CaJr93kK.legacy-office-r6.js'
PINS = {
    COMPONENT: 'f1fe525c2b9384c723e26c84ef7a78705ae12baa5d4d30c512f1a695a6ebccd2',
    STORE: '7431ad4e795530e64189f6a609c54f5446be791dd2bb686f3b67b222ddf03f90',
}
SUFFIX = '.members-r1'


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError('ASSET_ANCHOR_CHANGED: ' + old[:90])
    return source.replace(old, new, 1)


def component(source):
    assert '__wkMember' not in source
    source = replace(source, 'H=b(!1),te=b(""),O=b(!1)',
        'H=b(!1),te=b(""),__wkMemberPage=b(1),__wkMemberSize=b(20),O=b(!1)')
    source = replace(source, 'Be=z(()=>{const t=te.value.toLowerCase();',
        'Be=z(()=>{const t=te.value.trim().toLowerCase();')
    anchor = '});function Ke(t){return t.username||t.email||t.user_id}'
    paging = '''});
const __wkMemberWindow=z(()=>{const size=[20,50,100].includes(__wkMemberSize.value)?__wkMemberSize.value:20;
const pages=Math.max(1,Math.ceil(Be.value.length/size));
const page=Number.isFinite(__wkMemberPage.value)?Math.min(pages,Math.max(1,Math.floor(__wkMemberPage.value))):1;
const start=(page-1)*size;return{page,start,end:Math.min(Be.value.length,start+size)}});
const __wkMemberSlice=z(()=>Be.value.slice(__wkMemberWindow.value.start,__wkMemberWindow.value.end));
ge([te,__wkMemberSize],()=>{__wkMemberPage.value=1});
ge(__wkMemberWindow,t=>{__wkMemberPage.value=t.page});
function Ke(t){return t.username||t.email||t.user_id}'''
    source = replace(source, anchor, paging)
    source = replace(source, 'Re(e("div",gn,[', 'D.value==="members"?(d(),m("div",gn,[')
    source = replace(source, '"row-key":"id",data:Be.value,columns:_t.value',
        '"row-key":"id",data:__wkMemberSlice.value,columns:_t.value')
    source = replace(source, '["data","columns","loading"])]))])],512),[[je,D.value==="members"]])',
        '''["data","columns","loading"]),e("div",{class:"members-pagination",style:{padding:"16px 0",overflowX:"auto"}},[
n(j("t-pagination"),{current:__wkMemberPage.value,"onUpdate:current":o=>__wkMemberPage.value=o,
pageSize:__wkMemberSize.value,"onUpdate:pageSize":o=>__wkMemberSize.value=o,total:Be.value.length,
pageSizeOptions:[20,50,100],size:"small",showJumper:true,showPageNumber:true,showPageSize:true,onChange:jt})])]))])])):w("",!0)''')
    source = replace(source, 'const t=await ka(p.orgId);if(!p.visible)return;',
        'const __wkRequestedOrg=p.orgId,t=await ka(__wkRequestedOrg);if(!p.visible||p.orgId!==__wkRequestedOrg)return;')
    source = replace(source, 'D.value="basic",te.value="",ee.value="",',
        'D.value="basic",te.value="",__wkMemberPage.value=1,__wkMemberSize.value=20,ee.value="",')
    source = replace(source, 'He(),we(),$t())):Mt()', 'He(),$t())):(Mt(),k.clearCurrentOrganizationContext())')
    source = replace(source, 'ge(()=>p.orgId,()=>{p.visible&&jt()})',
        'ge(()=>p.orgId,()=>{if(p.visible){jt();D.value="basic";te.value="";__wkMemberPage.value=1;k.clearCurrentOrganizationContext();p.orgId&&!Y.value&&(He(),$t())}})')
    source = replace(source, 'ge(D,t=>{jt(),t==="joinRequests"&&p.orgId&&Je()})',
        'ge(D,t=>{jt(),t==="members"&&p.visible&&p.orgId&&we(),t==="joinRequests"&&p.orgId&&Je()})')
    source = replace(source, 'va(()=>{Mt()})', 'va(()=>{Mt();k.clearCurrentOrganizationContext()})')
    return source


def store(source):
    assert '__wkMemberRequest' not in source
    source = replace(source, 'async function ge(i){g.value=!0,f.value=null;',
        'let __wkMemberRequest=0;async function ge(i){const __wkRequest=++__wkMemberRequest;g.value=!0,f.value=null;')
    source = replace(source, 'const r=await Vt(i);return r.success&&r.data?',
        'const r=await Vt(i);if(__wkRequest!==__wkMemberRequest)return[];return r.success&&r.data?')
    source = replace(source, 'function Et(){t.value=null,n.value=[]}',
        'function Et(){__wkMemberRequest++;t.value=null,n.value=[]}')
    source = replace(source, 'function Bt(){e.value=[],t.value=null,n.value=[]',
        'function Bt(){__wkMemberRequest++;e.value=[],t.value=null,n.value=[]')
    return source


def build(source_root, output):
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'weknora-member-asset-patch.v1', 'files': [], 'production_modified': False}
    for name, transform in ((COMPONENT, component), (STORE, store)):
        raw = (source_root / name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == PINS[name], 'PRODUCTION_ASSET_HASH_CHANGED'
        patched = transform(raw.decode('utf-8')).encode('utf-8')
        (output / name).write_bytes(patched)
        report['files'].append({'name': name, 'baseline_sha256': PINS[name],
            'patched_sha256': hashlib.sha256(patched).hexdigest(), 'bytes': len(patched)})
    (output / 'PATCH.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-assets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source_assets, args.output), sort_keys=True))
