const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const crypto = require('node:crypto');
const vm = require('node:vm');
const fs = require('node:fs');
const {get} = require('../http-probe.cjs');
const hash = x => crypto.createHash('sha256').update(x).digest('hex');

async function serve(handler, callback) {
  const server = http.createServer(handler);
  await new Promise(r => server.listen(0,'127.0.0.1',r));
  try { await callback('http://127.0.0.1:'+server.address().port); }
  finally {server.closeAllConnections(); await new Promise(r => server.close(r));}
}
test('static status content-type and hash checked', async () => {
  await serve((q,r) => {r.writeHead(200,{'content-type':'image/png'}); r.end('icon');}, async base => {
    const out=await get(base,'image/png',hash('icon')); assert.equal(out.bytes,4);
    await assert.rejects(get(base,'javascript',hash('icon')),/content_type/);
    await assert.rejects(get(base,'image/png',hash('different')),/body_digest/);
  });
});
test('redirect not followed', async () => {
  let calls=0;
  await serve((q,r) => {calls++;r.writeHead(302,{location:'/secret'});r.end();}, async base => {
    await assert.rejects(get(base),/http_status_302/); assert.equal(calls,1);
  });
});
test('large body bounded', async () => {
  await serve((q,r) => r.end(Buffer.alloc(2*1024*1024+1)), async base => {
    await assert.rejects(get(base),/body_bound/);
  });
});
test('credentials rejected', async () => {
  await assert.rejects(get('http://user:password@127.0.0.1'),/invalid_target/);
});
test('total request deadline even without response', async () => {
  await serve(() => {}, async base => {
    const start=Date.now(); await assert.rejects(get(base),/request_deadline/);
    assert.ok(Date.now()-start<7000);
  });
});
test('node -e entry runs, import does not run; invalid remote host rejected', async () => {
  const src=fs.readFileSync(require.resolve('../http-probe.cjs'),'utf8');
  const errors=[]; let code;
  const context={require,module:{exports:{}},process:{argv:['node','evil.invalid','3000'],
    exit:c=>{code=c;},set exitCode(c){code=c;}},console:{error:s=>errors.push(s),log:()=>assert.fail('no success')},
    setTimeout,clearTimeout,URL,Buffer};
  vm.runInNewContext(src,context);
  await new Promise(r=>setImmediate(r));
  assert.equal(code,1); assert.deepEqual(errors,['bounded_http_probe_failed']);
});
