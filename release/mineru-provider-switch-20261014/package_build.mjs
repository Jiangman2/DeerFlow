import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

const here=path.dirname(fileURLToPath(import.meta.url));
const repo=path.resolve(here,'../..');
const output=process.argv[2] && path.resolve(process.argv[2]);
if (!output || fs.existsSync(output)) throw Error('fresh_output_required');
const read=p=>Buffer.from(fs.readFileSync(p,'utf8').replace(/\r\n/g,'\n'));
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const core=path.join(repo,'release/production-build-alignment-20260928/r4-build');
const frozen=['isolated_buildkit.py','build_resources.py','bounded_runc.py','resource_canary.py',
  'prepare_oci.py','stage_base.py','verify_oci_base.py','verify_built_image.py','layer_graph.py'];
const rows=frozen.map(n=>[n,read(path.join(core,n))]);
const frozenLock=JSON.parse(read(path.join(here,'build/BUILD-INFRA-LOCK.json')));
for(const [name,raw] of rows) if(sha(raw)!==frozenLock[name]) throw Error('frozen_helper_changed:'+name);
const provenance={source:'release/production-build-alignment-20260928/r4-build',
  reuse:'FROZEN_GENERIC_CONTROLS_ONLY',files:Object.fromEntries(rows.map(([n,b])=>[n,sha(b)]))};
rows.push(['BUILD-INFRA-PROVENANCE.json',Buffer.from(JSON.stringify(provenance,null,2)+'\n')]);
for (const name of ['BUILD-PLAN.json','BUILD-INFRA-LOCK.json','build_mineru.py','guard_mineru.py','apply_adapter.py','source_contract.py',
  'candidate_probe.py','verify_candidate.py','Dockerfile.mineru','RUNBOOK.txt','LOCAL-VERIFICATION.json']) {
  rows.push([name,read(path.join(here,'build',name))]);
}
rows.push(['verify_base_export.py',read(path.join(here,'verify_base_export.py'))]);
const payload=read(path.join(repo,'docker/mineru_adapter/app.py'));
const plan=JSON.parse(read(path.join(here,'build/BUILD-PLAN.json')));
if(sha(payload)!==plan.source.after_sha256) throw Error('candidate_source_drift');
rows.push(['payload-adapter.py',payload]);
fs.mkdirSync(output);
const bundle=path.join(output,'bundle');
fs.mkdirSync(bundle);
for(const [name,raw] of rows) fs.writeFileSync(path.join(bundle,name),raw,{flag:'wx'});
const manifest=rows.map(([n,b])=>`${sha(b)}  ${n}`).sort().join('\n')+'\n';
fs.writeFileSync(path.join(bundle,'PACKAGE.SHA256'),manifest,{flag:'wx'});
const archive=path.join(output,'deerflow-mineru-dedicated-async-build-20261015-r1-fix1.tar.gz');
execFileSync('tar',['-czf',archive,'-C',bundle,...rows.map(x=>x[0]).sort(),'PACKAGE.SHA256']);
const digest=sha(fs.readFileSync(archive));
fs.writeFileSync(archive+'.sha256',`${digest}  ${path.basename(archive)}\n`,{flag:'wx'});
console.log(JSON.stringify({archive,sha256:digest,files:rows.length+1,
  package_manifest_sha256:sha(Buffer.from(manifest)),build_started:false,production_workloads_mutated:false}));
