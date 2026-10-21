import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';

const source = path.dirname(fileURLToPath(import.meta.url));
if (process.argv.length !== 3) throw Error('one_fresh_output_directory_required');
const output = path.resolve(process.argv[2]);
if (fs.existsSync(output)) throw Error('delivery_exists_do_not_overwrite');
const names = ['export_base.py', 'verify_base_export.py', 'inspect_build_input.py',
  'BASE-EXPORT-RUNBOOK.txt', 'BASE-EXPORT-VERIFICATION.json'];
const members = names.map(name => [name, fs.readFileSync(path.join(source, name))]);
const hash = raw => crypto.createHash('sha256').update(raw).digest('hex');
fs.mkdirSync(output);
const bundle = path.join(output, 'bundle');
fs.mkdirSync(bundle);
for (const [name, raw] of members) fs.writeFileSync(path.join(bundle, name), raw, {flag:'wx'});
const manifest = members.map(([name, raw]) => `${hash(raw)}  ${name}`).sort().join('\n')+'\n';
fs.writeFileSync(path.join(bundle, 'PACKAGE.SHA256'), manifest, {flag:'wx'});
const archive = path.join(output, 'deerflow-mineru-base-export-20261014-r1.tar.gz');
execFileSync('tar', ['-czf', archive, '-C', bundle, ...names, 'PACKAGE.SHA256']);
const archiveHash = hash(fs.readFileSync(archive));
fs.writeFileSync(archive+'.sha256', `${archiveHash}  ${path.basename(archive)}\n`, {flag:'wx'});
console.log(JSON.stringify({archive, sha256:archiveHash, members:names.length+1,
  production_workloads_mutated:false, build_started:false}));
