"""Freeze the exact field-bound notification and pure Nginx transformation inputs."""
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]

def main():
    original=(HERE.parent/'html-reader-20261114-r1/nginx_reader.py').read_text(encoding='utf-8')
    marker="    start = source.rfind('\\n', 0, facts['api_location_start']) + 1\n    indent = source[start:facts['api_location_start']]"
    assert original.count(marker)==1
    replacement="""    # Put the reader before every existing server location so an older
    # general .html regex cannot capture this new route first.
    server = next(node for node in descendants(parse(source)) if node.name == 'server' and node.start == facts['server_start'])
    anchor = min(node.start for node in server.children if node.name == 'location')
    start = source.rfind('\\n', 0, anchor) + 1
    indent = source[start:anchor]"""
    reader=original.replace(marker,replacement)
    target=HERE/'nginx_reader.py'
    if target.exists():reader=target.read_text(encoding='utf-8');compile(reader,str(target),'exec')
    else:target.write_bytes(reader.encode())
    # This pure parser is reused only to validate/replay the existing seven
    # sed substitutions. The historical SkillHub activation is never run.
    source=REPO/'.codex-post44-sync-20261113/deploy/enterprise/skillhub_nginx_auth_transform_20261106_r2.py'
    raw=source.read_bytes();compile(raw,str(source),'exec')
    target=HERE/'startup_policy.py'
    if target.exists():assert target.read_bytes()==raw,'REVIEWED_STARTUP_POLICY_CHANGED'
    else:target.write_bytes(raw)
    print(json.dumps({'reader_sha256':hashlib.sha256(reader.encode()).hexdigest(),'startup_policy_sha256':hashlib.sha256(raw).hexdigest()}))

if __name__=='__main__':main()
