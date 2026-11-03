"""Read bounded compiled feature assets from the qualified local image, offline."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

TAG = "deerflow.local/deer-flow-frontend:20261103-dashboard-navigation-r2"
MARKERS = {
    "history_label": "历史对话",
    "history_english": "Chat history",
    "section_runs": "运行诊断",
    "section_users": "用户与员工",
    "section_knowledge": "知识库",
    "section_models": "模型监控",
    "section_oa": "OA 接口",
    "failure_range": "最新 1000 条范围内筛选",
    "responsive_navigation": "sm:grid-cols-6",
    "custom_range": "自定义区间",
    "date_bounds": "/dashboard/date-bounds",
    "failure_preview_bound": "失败运行列表过大，已停止加载以保护页面",
    "personal_heatmap": "activity-heatmap",
    "weekly_heatmap": "data-week-start",
    "account_activity": "/console/activity",
    "model_api": "/admin/model-monitor",
    "oa_monitor": "oa-policy-flow-monitor",
    "web_opt_in": "已开启联网搜索",
    "multiline_draft": "height:72px!important",
    "single_material_row": "max-height:48px",
}
PROBE = r"""
const fs=require('fs'),crypto=require('crypto');
const root='/app/frontend', markers=JSON.parse(process.argv[1]);
const found=Object.fromEntries(Object.keys(markers).map(k=>[k,[]])), pins={};
let total=0;
function read(path){
 if(fs.realpathSync(path)!==path)throw Error('asset_symlink');
 const fd=fs.openSync(path,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try {const s=fs.fstatSync(fd);
  if(!s.isFile()||s.size>2097152)throw Error('asset_size');
  total+=s.size;if(total>67108864)throw Error('aggregate_size');
  return fs.readFileSync(fd);
 } finally {fs.closeSync(fd);}
}
function pin(path,raw){pins[path]={bytes:raw.length,sha256:crypto.createHash('sha256').update(raw).digest('hex')};}
const files=fs.readdirSync(root+'/.next/static/chunks').sort();
if(files.length>2048)throw Error('file_count');
for(const file of files){
 if(!/^[a-zA-Z0-9_.-]+\.(js|css)$/.test(file))continue;
 const path=root+'/.next/static/chunks/'+file,raw=read(path),text=raw.toString('utf8');
 let used=false;
 for(const [name,marker] of Object.entries(markers))if(text.includes(marker)){found[name].push(path);used=true;}
 if(used)pin(path,raw);
}
for(const [name,rows] of Object.entries(found))if(!rows.length)throw Error('missing_marker:'+name);
for(const suffix of ['/.next/BUILD_ID','/.next/routes-manifest.json','/public/images/cnooc-workspace-concept-bg.png']){
 const path=root+suffix;pin(path,read(path));
}
if(Object.keys(pins).length>32)throw Error('pin_count');
console.log(JSON.stringify({source_pins:pins,feature_markers:found}));
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualified", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = (args.qualified / "RESULT.json").read_bytes()
    result = json.loads(raw)
    assert result["frontend_slim"] == "LOCAL_PASS" and result["platform"] == "linux/arm64"
    assert result["image"]["tag"] == TAG and result["export"]["exact_qualified_layers"] == "PASS"
    observed = subprocess.check_output([
        "docker", "run", "--rm", "--pull=never", "--network=none", "--platform=linux/arm64",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--memory=256m",
        "--pids-limit=64", "--entrypoint=node", result["image"]["id"], "-e", PROBE,
        json.dumps(MARKERS, ensure_ascii=False)], timeout=90)
    assert 0 < len(observed) < 65536
    assets = json.loads(observed)
    assets.update(schema="portal-dashboard-navigation-frontend-assets.v1",
                  status="COMPILED_MARKERS_PINNED", image=result["image"]["id"],
                  qualification_sha256=hashlib.sha256(raw).hexdigest(),
                  probe_sha256=hashlib.sha256(PROBE.encode()).hexdigest(),
                  production_access=False, behavior_acceptance_claimed=False)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(assets, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": assets["status"], "files": len(assets["source_pins"]),
                      "features": len(assets["feature_markers"])}))


if __name__ == "__main__":
    main()
