import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
const [rootArg, manifestFile, receiptFile] = process.argv.slice(2);
const root = fs.realpathSync(rootArg);
const hash = text => createHash('sha256').update(text).digest('hex');
const count = (text, part) => text.split(part).length - 1;
const entries = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
const prepared = entries.map(entry => {
  const target = fs.realpathSync(path.resolve(root, entry.path));
  if (!target.startsWith(root + path.sep) || !fs.statSync(target).isFile()) throw Error('Unsafe path: ' + entry.path);
  let text = fs.readFileSync(target, 'utf8').replace(/\r\n/g, '\n');
  const before = hash(text);
  for (const [index, edit] of entry.edits.entries()) {
    if (count(text, edit.before) === 1) text = text.replace(edit.before, () => edit.after);
    else if (count(text, edit.before) === 0 && count(text, edit.after) === 1) continue;
    else throw Error(`Baseline mismatch: ${entry.path} edit ${index}`);
  }
  return {target, text, path: entry.path, before, after: hash(text)};
});
for (const entry of prepared) {
  const temporary = entry.target + '.channel-pdf-r1.tmp';
  fs.writeFileSync(temporary, entry.text, {encoding:'utf8', flag:'wx', mode:fs.statSync(entry.target).mode});
  fs.renameSync(temporary, entry.target);
}
fs.mkdirSync(path.dirname(receiptFile), {recursive:true});
fs.writeFileSync(receiptFile, JSON.stringify(prepared.map(({path,before,after}) => ({path,before,after})), null, 2));
console.log(`frontend_exact_delta=PASS files=${prepared.length}`);
