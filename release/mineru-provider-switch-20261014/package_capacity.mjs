// Reproducible, allowlisted diagnostic archive. Never packages credentials or runtime payloads.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const output = process.argv[2] && path.resolve(process.argv[2]);
if (!output || fs.existsSync(output)) throw Error('new_output_directory_required');
const names = ['audit_mineru.py', 'audit_capacity.py', 'run-datax01-capacity.sh',
  'CAPACITY-RUNBOOK.txt', 'CAPACITY-VERIFICATION.json'];
const sha = raw => crypto.createHash('sha256').update(raw).digest('hex');
const rows = names.map(name => {
  const file = path.join(here, name), s = fs.lstatSync(file);
  if (!s.isFile() || s.isSymbolicLink() || s.size > 1048576) throw Error('unsafe_source');
  return [name, Buffer.from(fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n'))];
});
fs.mkdirSync(output);
const content = path.join(output, 'content');
fs.mkdirSync(content);
for (const [name, raw] of rows) fs.writeFileSync(path.join(content, name), raw, {flag: 'wx'});
const manifest = rows.map(([name, raw]) => `${sha(raw)}  ${name}`).sort().join('\n') + '\n';
fs.writeFileSync(path.join(content, 'PACKAGE.SHA256'), manifest, {flag: 'wx'});
const filename = 'deerflow-mineru-capacity-20261014-r1.tar.gz';
const archive = path.join(output, filename);
execFileSync('tar', ['-czf', archive, '-C', content, ...[...names, 'PACKAGE.SHA256'].sort()], {stdio:'pipe'});
const digest = sha(fs.readFileSync(archive));
fs.writeFileSync(archive + '.sha256', `${digest}  ${filename}\n`, {flag:'wx'});
console.log(JSON.stringify({archive, sha256:digest, files:names.length+1,
  candidate_code_included:false, production_workloads_mutated:false}));
