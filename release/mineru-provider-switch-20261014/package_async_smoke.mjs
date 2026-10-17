import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../..');
const output = process.argv[2] && path.resolve(process.argv[2]);
if (!output || fs.existsSync(output)) throw Error('new_output_directory_required');
const sha = raw => crypto.createHash('sha256').update(raw).digest('hex');
const names = ['audit_mineru.py', 'audit_capacity.py', 'probe_async.py', 'async_probe_remote.py',
  'run-datax01-async-smoke.sh', 'ASYNC-SMOKE-RUNBOOK.txt', 'SMOKE-VERIFICATION.json'];
const read = file => {
  const s = fs.lstatSync(file);
  if (!s.isFile() || s.isSymbolicLink() || s.size > 2097152) throw Error('unsafe_source');
  return Buffer.from(fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n'));
};
const rows = names.map(name => [name, read(path.join(here, name))]);
rows.push(['candidate-adapter.py', read(path.join(root, 'docker/mineru_adapter/app.py'))]);
const lock = {schema:'mineru-async-smoke-source-lock-v1',
  source_sha256:Object.fromEntries(rows.map(([name, raw]) => [name, sha(raw)])),
  production_source_sha256:'1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39',
  runtime_installation:false, maximum_provider_submissions:1, ready_for_rollout:false};
rows.push(['SMOKE-LOCK.json', Buffer.from(JSON.stringify(lock,null,2)+'\n')]);
fs.mkdirSync(output);
const content = path.join(output, 'content');
fs.mkdirSync(content);
for (const [name, raw] of rows) fs.writeFileSync(path.join(content, name), raw, {flag:'wx'});
const manifest = rows.map(([name, raw]) => `${sha(raw)}  ${name}`).sort().join('\n')+'\n';
fs.writeFileSync(path.join(content,'PACKAGE.SHA256'),manifest,{flag:'wx'});
const filename = 'deerflow-mineru-async-smoke-20261014-r1.tar.gz';
const archive = path.join(output, filename);
execFileSync('tar',['-czf',archive,'-C',content,...[...rows.map(x=>x[0]),'PACKAGE.SHA256'].sort()],{stdio:'pipe'});
const digest = sha(fs.readFileSync(archive));
fs.writeFileSync(archive+'.sha256',`${digest}  ${filename}\n`,{flag:'wx'});
console.log(JSON.stringify({archive,sha256:digest,files:rows.length+1,
  maximum_provider_submissions:1,production_workloads_mutated:false}));
