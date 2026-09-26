// Read-only, bounded static-resource and health checks. No cookies, auth, redirects,
// proxy inheritance, remote code, response text, subprocesses or filesystem writes.
const fs = require('node:fs'), path = require('node:path');
const http = require('node:http'), crypto = require('node:crypto');
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const MAX = 2 * 1024 * 1024;
const root = '/app/frontend';
function firstJS(dir, budget = {left: 4096}) {
  for (const name of fs.readdirSync(dir).sort()) {
    if (--budget.left < 0) throw Error('asset_scan_bound');
    const file = path.join(dir, name), st = fs.lstatSync(file);
    if (st.isFile() && name.endsWith('.js') && st.size > 0 && st.size <= MAX) return file;
    if (st.isDirectory()) { const found = firstJS(file, budget); if (found) return found; }
  }
}
function get(url, type, expected) {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    if (target.protocol !== 'http:' || target.username || target.password) return reject(Error('invalid_target'));
    let total = 0; const chunks = [];
    const req = http.get(target, {agent:false}, res => {
      if (res.statusCode !== 200) { res.destroy(); return reject(Error('http_status_' + res.statusCode)); }
      if (type && !(res.headers['content-type'] || '').toLowerCase().includes(type)) {
        res.destroy(); return reject(Error('content_type'));
      }
      res.on('data', chunk => {
        total += chunk.length;
        if (total > MAX) res.destroy(Error('body_bound')); else chunks.push(chunk);
      });
      res.on('error', reject);
      res.on('end', () => {
        const digest = hash(Buffer.concat(chunks));
        if (expected && digest !== expected) return reject(Error('body_digest'));
        resolve({status:200, bytes:total, sha256:digest});
      });
    });
    const timer = setTimeout(() => req.destroy(Error('request_deadline')), 5000);
    req.on('close', () => clearTimeout(timer));
    req.on('error', reject);
  });
}
async function main() {
  const [host, port, health] = process.argv.slice(1);
  if (!/^(127\.0\.0\.1|frontend\.deer-flow\.svc|cap-w1-fe-[0-9a-f]{16}\.deer-flow\.svc)$/.test(host) || port !== '3000') throw Error('target_not_allowed');
  if (health && !/^\/(health|live|ready|api\/health)$/.test(health)) throw Error('health_path_not_allowed');
  const icon = fs.readFileSync(root+'/public/images/cnooc-haineng-mark.png');
  if (icon.length > MAX) throw Error('icon_bound');
  const asset = firstJS(root+'/.next/static');
  if (!asset) throw Error('asset_missing');
  const js = fs.readFileSync(asset);
  const base = 'http://'+host+':'+port;
  const out = {target:host,
    icon:await get(base+'/images/cnooc-haineng-mark.png', 'image/png', hash(icon)),
    static_js:await get(base+'/_next/static/'+path.relative(root+'/.next/static',asset).split(path.sep).map(encodeURIComponent).join('/'), 'javascript', hash(js))};
  if (health) out.gateway_health = await get('http://gateway.deer-flow.svc:8001'+health);
  console.log(JSON.stringify(out));
}
if (!module.parent) {
  const deadline = setTimeout(() => { console.error('probe_deadline'); process.exit(1); }, 20000);
  main().then(() => clearTimeout(deadline)).catch(() => {clearTimeout(deadline); console.error('bounded_http_probe_failed'); process.exitCode=1;});
}
module.exports = {get, firstJS};
