"""Seal a new exact-run, enum-only diagnostic. Historical packages stay immutable."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PREVIOUS = HERE.parent / 'delivery-sequence-20261114-r1'
spec = importlib.util.spec_from_file_location('sequence_r1_seal', PREVIOUS / 'seal.py')
prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)

EVENT_SQL = '''WITH selected AS (
 SELECT seq,event_type,created_at,octet_length(content) AS bytes
 FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) AND seq=ANY($4::integer[])),
 budget AS (SELECT *,sum(least(bytes,32768)) OVER (ORDER BY seq DESC) AS cumulative_bytes FROM selected)
SELECT b.seq,b.event_type,b.created_at,b.bytes,
 CASE WHEN b.bytes<=32768 AND b.cumulative_bytes<=524288 THEN e.content ELSE NULL END AS content,
 CASE WHEN b.bytes>32768 AND b.cumulative_bytes<=524288 THEN encode(substring(convert_to(e.content,'UTF8') FROM 1 FOR 8192),'base64') ELSE NULL END AS head_b64,
 CASE WHEN b.bytes>32768 AND b.cumulative_bytes<=524288 THEN encode(substring(convert_to(e.content,'UTF8') FROM b.bytes-8192+1 FOR 8192),'base64') ELSE NULL END AS tail_b64
FROM budget b JOIN run_events e ON e.thread_id=$1 AND e.run_id=$2 AND e.seq=b.seq ORDER BY b.seq LIMIT 26'''


def child_source():
    raw = (PREVIOUS / 'inspect_delivery_child.py').read_text(encoding='utf-8')
    start = raw.index('EVENT_SQL = """'); end = raw.index('"""', start+len('EVENT_SQL = """'))+3
    raw = raw[:start] + 'EVENT_SQL = ' + repr(EVENT_SQL) + raw[end:]
    marker = "if __name__=='__main__':\n    sys.exit(main())"
    assert raw.count(marker) == 1
    raw = raw.replace(marker, (HERE / 'details.py').read_text(encoding='utf-8') + '\n' + marker)
    raw = raw.replace('energy-delivery-sequence-child.v1', 'energy-delivery-detail-child.v2')
    compile(raw, '<detail-child>', 'exec'); ast.parse(raw, feature_version=(3,7))
    return raw.encode()


def build():
    child = child_source(); raw = prior.source_host()
    raw = raw.replace(b'energy-delivery-sequence-inspection.v1', b'energy-delivery-detail-inspection.v2')
    raw = raw.replace(b'READ_FIXED_THREAD_DELIVERY_SEQUENCE', b'READ_FIXED_RUN_DELIVERY_DETAILS')
    assets = {'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    assert raw.count(b'ASSETS={}\n') == 1
    sealed = raw.replace(b'ASSETS={}\n', b'ASSETS='+repr(assets).encode()+b'\n')
    compile(sealed,'<sealed-detail>','exec');ast.parse(sealed.decode(),feature_version=(3,7))
    return sealed, child


def main():
    sealed,child=build()
    directory=ROOT/'release/energy-delivery-detail-inspection-20261114-r2'
    directory.mkdir(exist_ok=True)
    target=directory/'inspect-energy-delivery-detail-20261114-r2.py'
    meta={'schema':'energy-delivery-detail-package.v2','filename':target.name,'bytes':len(sealed),
          'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),
          'fixed_thread':'8e27d29f-730b-4a89-9681-36fdddc87887',
          'fixed_run_sha256':'03c66c03186bbf7365ee886846fc816c0b18ee85648c66af7629fb0e80be8ab5',
          'fixed_sequences':[87,88,89,90]+list(range(99,121)),
          'production_executed':False,'messages_sent':0,'mutations_supported':False,
          'raw_error_or_code_or_message_text_exported':False}
    for path,data in ((target,sealed),(target.with_suffix('.package.json'),(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())):
        if path.exists(): assert path.read_bytes()==data,'EXISTING_PACKAGE_DIFFERS'
        else: path.write_bytes(data)
    print(json.dumps(meta,sort_keys=True))


if __name__=='__main__':main()
