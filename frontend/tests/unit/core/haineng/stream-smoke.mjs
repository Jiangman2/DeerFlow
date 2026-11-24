import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";
const source = stripTypeScriptTypes(readFileSync(process.argv[2], "utf8"));
const { consumeHainengStream } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
function stream(text) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({start(c) { for (const byte of bytes) c.enqueue(Uint8Array.of(byte)); c.close(); }});
}
const events=[];
await consumeHainengStream(stream('data:{"header":{"code":0},"payload":{"data":{"choices":{"text":"你好"}}}}\r\n\r\ndata:{"header":{"code":10000}}\r\n\r\n'),e=>events.push(e));
assert.equal(events[0].payload.data.choices.text,'你好');
assert.equal(events.length,2);
await assert.rejects(consumeHainengStream(stream('data:{"header":{"code":0}}\n\n'),()=>{}), /提前中断/);
await assert.rejects(consumeHainengStream(stream('data:{"header":{"code":5002,"message":"审核拦截"}}\n\n'),()=>{}), /审核拦截/);
console.log('PASS: UTF-8 split chunks, CRLF frames, completion, interrupted stream, business errors');
