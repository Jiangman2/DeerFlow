"""Verify and archive the operator's returned installation JSON, unchanged."""
import hashlib
import json
from pathlib import Path

REPO=Path(__file__).resolve().parents[3]
ATTACHMENT=Path('C:/Users/15285/.codex/attachments/856530e9-dd22-4b2c-a61b-2fdbed4b1a59/Pasted text.txt')
DEST=REPO/'release/energy-html-reader-and-skill-20261114-r1'

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(name,raw):
    path=DEST/name
    if path.exists():assert path.read_bytes()==raw,'EXISTING_ARCHIVE_DIFFERS'
    else:path.write_bytes(raw)

def main():
    raw=ATTACHMENT.read_bytes();lines=raw.decode('utf-8-sig').splitlines()
    rows=[json.loads(s) for s in lines if s.startswith('{')]
    skill=next(r for r in rows if r.get('schema')=='energy-programmatic-skill-install.v1')
    expected=skill['report_sha256'];size=skill['report_bytes']
    saved={k:v for k,v in skill.items() if k not in ('report_path','report_sha256','report_bytes')}
    receipt=canonical(saved)
    assert len(receipt)==size==9671 and sha(receipt)==expected=='e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb'
    assert saved['publication']['after_tree_sha256']=='67b0cc44e9aec87fa4fa12274704f59baa636ccd566e55d8cb79cad23efbfeaa'
    reader=next(r for r in rows if r.get('schema')=='energy-html-reader-activation.v1')
    reader_saved={k:v for k,v in reader.items() if k not in ('report_path','report_sha256')}
    assert sha(canonical(reader_saved))==reader['report_sha256']
    assert sha(canonical(reader))==skill['accepted_html_reader']['receipt_sha256']
    save('OPERATOR-INSTALL-OUTPUT.txt',raw)
    save('OPERATOR-SKILL-INSTALL-REPORT.json',receipt)
    save('OPERATOR-READER-INSTALL-REPORT.json',canonical(reader_saved))
    save('OPERATOR-READER-RUNTIME-ACCEPTANCE.json',canonical(reader))
    save('OPERATOR-COMBINED-RESULT.json',canonical(rows[-1]))
    print(json.dumps({'skill_receipt_exact':True,'skill_receipt_bytes':size,'skill_receipt_sha256':expected,
                      'reader_receipt_exact':True,'ordinary_sandbox_visibility_pending':True,'business_acceptance_pending':True}))

if __name__=='__main__':main()
