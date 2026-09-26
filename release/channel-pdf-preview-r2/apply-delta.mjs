import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
const [rootArg, manifestFile, receiptFile, mode] = process.argv.slice(2);
const checkOnly = mode === '--check-only';
if (mode && !checkOnly) throw Error('Unknown mode');
const root = fs.realpathSync(rootArg);
const hash = value => createHash('sha256').update(value).digest('hex');
const count = (text, part) => text.split(part).length - 1;
const entries = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
const prepared = entries.map(entry => {
  const target = fs.realpathSync(path.resolve(root, entry.path));
  if (!target.startsWith(root + path.sep) || !fs.statSync(target).isFile()) throw Error('Unsafe path: ' + entry.path);
  const raw = fs.readFileSync(target), before = hash(raw);
  if (![entry.source_sha256, entry.target_sha256].includes(before)) throw Error('Audited source hash mismatch: ' + entry.path);
  let text = raw.toString('utf8').replace(/\r\n/g, '\n');
  if (before !== entry.target_sha256) {
    for (const [index, edit] of entry.edits.entries()) {
      if (!edit.before || count(text, edit.before) !== 1) throw Error(`Baseline mismatch: ${entry.path} edit ${index}`);
      text = text.replace(edit.before, () => edit.after);
    }
  }
  if (hash(text) !== entry.target_sha256) throw Error('Audited target hash mismatch: ' + entry.path);
  const temporary = target + '.channel-pdf-r2.tmp';
  if (fs.existsSync(temporary)) throw Error('Temporary file already exists: ' + temporary);
  return { target, temporary, text, path: entry.path, before, after: hash(text) };
});
if (!checkOnly) {
  for (const entry of prepared) {
    fs.writeFileSync(entry.temporary, entry.text, {encoding:'utf8', flag:'wx', mode:fs.statSync(entry.target).mode});
    fs.renameSync(entry.temporary, entry.target);
  }
  fs.mkdirSync(path.dirname(receiptFile), {recursive:true});
  fs.writeFileSync(receiptFile, JSON.stringify(prepared.map(({path,before,after}) => ({path,before,after})), null, 2));
}
console.log(`frontend_audited_delta=PASS files=${prepared.length} mode=${checkOnly ? 'check-only' : 'apply'}`);
