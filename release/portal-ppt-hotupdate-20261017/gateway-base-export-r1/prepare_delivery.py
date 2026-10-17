"""Publish the reviewed operator tool without modifying its frozen bytes."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEST = Path("C:/Users/15285/Downloads/portal-ppt-gateway-base-20261017-r1")
NAME = "export-portal-ppt-gateway-base-20261017-r1.py"
SHA = "23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f"
COMMAND = "sudo python3 -I -B -u /home/cnooc/" + NAME + " --export"


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def main():
    raw = (ROOT / "export_gateway_base.py").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA
    review = json.loads((ROOT / "INDEPENDENT-REVIEW.json").read_text(encoding="utf-8"))
    assert review["status"] == "REVIEWED_FOR_OPERATOR_EXPORT" and review["script_sha256"] == SHA
    DEST.mkdir(exist_ok=False)
    with (DEST / NAME).open("xb") as out:
        out.write(raw)
    guide = (
        "Gateway 固定基底导出（2026-10-17 r1）\n\n"
        "将本目录的 Python 文件传到 ecs-zdz-pro-0020 的 /home/cnooc/，保持文件名。\n"
        "请在 ecs-zdz-pro-0020（不是 datax-01）执行完整一行：\n\n" + COMMAND + "\n\n"
        "工具只导出已核对的本地 Gateway 镜像到一个新建私有目录，并核验全部归档摘要；不更新服务。\n"
        "结束后贴回完整终端输出，并按 report_path 返回 REPORT.json。\n"
        "镜像包 gateway.base.oci.tar 留在该服务器，后续作为构建基底；无需上传聊天。\n"
        "失败时保留输出及目录，先审阅，不自动重跑。\n\n"
        "Python 文件 SHA256：" + SHA + "\n"
        "单次导出至多15分钟，总处理上限20分钟。磁盘不足或身份变化会停止。\n"
    )
    with (DEST / "README.txt").open("x", encoding="utf-8", newline="\n") as out:
        out.write(guide)
    with (DEST / "SHA256SUMS.txt").open("x", encoding="ascii", newline="\n") as out:
        out.write(SHA + "  " + NAME + "\n")
    assert ref(DEST / NAME)["sha256"] == SHA
    receipt = {
        "schema": "portal-ppt-gateway-base-export-delivery.v1",
        "status": "READY_FOR_OPERATOR_EXECUTION_NOT_YET_EXECUTED",
        "script": ref(DEST / NAME),
        "guide": ref(DEST / "README.txt"),
        "checksum_file": ref(DEST / "SHA256SUMS.txt"),
        "independent_review": ref(ROOT / "INDEPENDENT-REVIEW.json"),
        "offline_qualification": ref(ROOT / "LOCAL-VERIFICATION.json"),
        "worker": "ecs-zdz-pro-0020", "command": COMMAND,
        "return": ["complete terminal output", "REPORT.json"],
        "private_oci": "Keep on server for later build input; do not upload to chat",
        "automatic_retry": False, "production_execution": False,
        "production_service_mutation": False, "ready_for_build": False, "ready_for_rollout": False
    }
    with (ROOT.parent / "DELIVERY-gateway-base-r1.json").open("x", encoding="utf-8", newline="\n") as out:
        json.dump(receipt, out, ensure_ascii=False, indent=2)
        out.write("\n")
    print(json.dumps({"script": receipt["script"], "command": COMMAND}))


if __name__ == "__main__":
    main()
