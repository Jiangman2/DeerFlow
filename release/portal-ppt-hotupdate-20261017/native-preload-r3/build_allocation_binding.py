import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
PREP=HERE.parent
def read(path):
    raw=path.read_bytes()
    return json.loads(raw),hashlib.sha256(raw).hexdigest()

def build():
    allocation,allocation_sha=read(PREP/'native-allocation-review-r1/ALLOCATION.json')
    assert allocation_sha=='304460fa19e0b62e4193bebd03316950de2324a1d4f6496ad944c83cc844d1d6'
    catalog,catalog_sha=read(PREP/'native-cache-diagnostic-r1/CATALOG.json')
    assert catalog_sha=='f68a13ce2bd6f50746ccdea53bf2f7b99627f2579c34f9f81b8d1e6b968ab5df'
    report,report_sha=read(PREP/'native-cache-evidence-r1/REPORT.json')
    assert report_sha=='5a3b7b992a170204247d27a33b740b78d65f0d89c443032020669023c3e7ba15'
    hardlinks,hardlinks_sha=read(PREP/'native-allocation-review-r1/HARDLINK-REVIEW.json')
    assert hardlinks_sha=='3cd5e14cc42683514644e3e5ede2c7abb1f9fdb60f70df4bf010306c555d7496'
    assert hardlinks['requires_lower_target_count']==0 and hardlinks['hardlinks']==44
    assert all(r['all_hardlinks_resolve_to_prior_same_layer_regular'] and r['compressed_sha256_verified']
               and r['diff_id_verified'] for r in hardlinks['rows'])
    amounts={}
    wanted=set(c for i in allocation['images'] for c in i['missing_chains'])
    for image in catalog['images']:
        parent=''
        for index in image['layer_indices']:
            layer=catalog['layers'][index]
            diff=layer['diff_id']
            chain=diff if not parent else 'sha256:'+hashlib.sha256((parent+' '+diff).encode()).hexdigest()
            parent=chain
            if chain not in wanted:
                continue
            row=allocation['blobs'][layer['blob_digest']]
            assert row['compressed_digest_verified'] and row['diff_id_verified'] and row['diff_id']==diff
            assert row['sparse']==row['duplicate_entry_paths']==0
            implicit=row['parents_not_explicit_directories']
            amount=(row['regular_4k_roundup_bytes']+4096*(row['explicit_unique_directory_paths']+implicit)
                +4096*row['symlinks']+row['xattr_per_file_4k_roundup_bytes']
                +4096*(row['entries']+implicit)+4096*row['whiteout_markers'])
            encoding=[layer['blob_digest'],layer['compressed_bytes']]
            if chain in amounts:
                assert amounts[chain][0]==diff and amounts[chain][2]==amount
                if encoding not in amounts[chain][1]:amounts[chain][1].append(encoding)
            else:
                amounts[chain]=[diff,[encoding],amount]
    assert len(amounts)==93 and set(amounts)==wanted
    binding=dict(allocation_sha256=allocation_sha,hardlink_review_sha256=hardlinks_sha,
        plan_sha256=report['plan_sha256'],chains=amounts)
    (HERE/'ALLOCATION-BINDING.json').write_text(json.dumps(binding,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    template=(HERE/'budget_runtime_template.py').read_text(encoding='utf-8')
    output=template.replace('@@BINDING_JSON@@',json.dumps(binding,separators=(',',':'),sort_keys=True))
    (HERE/'budget_runtime.py').write_text(output,encoding='utf-8',newline='\n')
    print(json.dumps(dict(chains=len(amounts),physical_allowance=sum(r[2] for r in amounts.values())+16*1024**2,
        allocation_sha256=allocation_sha,hardlink_review_sha256=hardlinks_sha)))

if __name__=='__main__':build()
