"""Deliver the reviewed read-only collector with its fixed byte identity."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "native-preflight-r1"
DEST = Path("C:/Users/15285/Downloads/portal-ppt-native-preflight-20261017-r1")
NAME = "collect-portal-ppt-native-preflight-20261017-r1.py"
PINS = {
    "collect_native_preflight.py": "79460b994738d4abcd773f2e5223977c8b0dead109926bafcb447015de7e6b80",
    "test_collect_native_preflight.py": "3742b9f089e772b6d44841bfe5c6f6646400c5e708b7d001a58a83b650b02c82",
    "LOCAL-VERIFICATION.json": "c962ee91875900dde6fae96db2d4ba5fef145d08eb73b0660ac55a9f0263fbea"
}
COMMAND = "(umask 077; set -C; sudo python3 -I -B -u /home/cnooc/" + NAME + " --collect > /home/cnooc/portal-ppt-native-preflight-20261017-r1.REPORT.json)"


def main():
    for filename, expected in PINS.items():
        assert hashlib.sha256((SOURCE / filename).read_bytes()).hexdigest() == expected, filename
    qualification = json.loads((SOURCE / "LOCAL-VERIFICATION.json").read_bytes())
    assert qualification["final_pass"] == qualification["final_test_count"] == 18
    assert qualification["final_exitcode"] == qualification["final_failures"] == 0
    payload = (SOURCE / "collect_native_preflight.py").read_bytes()
    assert len(payload) == 33725
    DEST.mkdir(exist_ok=False)
    with (DEST / NAME).open("xb") as stream:
        stream.write(payload)
    assert (DEST / NAME).read_bytes() == payload
    instructions = (
        "ARM64 隔离验收前的只读检查\n\n"
        "将本目录的 " + NAME + " 上传到 datax-01 的 /home/cnooc/。\n"
        "在普通 cnooc shell 整行执行（不要在整行外再加 sudo）：\n" + COMMAND + "\n\n"
        "固定读取 datax-02、四个应用 Deployment、PostgreSQL 身份和节点资源概要。\n"
        "不会启动测试容器、导入镜像、执行迁移、修改服务、读取配置内容或输出环境变量值。\n"
        "进度及最终 SHA/退出码显示在终端；报告由 cnooc shell 以0600创建，不覆盖同名旧文件。\n"
        "请贴回终端摘要，并下载 /home/cnooc/portal-ppt-native-preflight-20261017-r1.REPORT.json。\n"
        "不要将stderr重定向进报告；若失败或中断，保留报告和终端输出，先不要重跑。\n"
        "最多约6分钟。完成采集不等于通过原生运行验收或允许切换生产。\n"
    )
    with (DEST / "README.txt").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(instructions)
    with (DEST / "SHA256SUMS.txt").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(PINS["collect_native_preflight.py"] + "  " + NAME + "\n")
    review = {
        "schema": "portal-ppt-native-preflight-reviewed-delivery.v1",
        "status": "REVIEWED_DELIVERED_FOR_FIXED_READ_ONLY_COLLECTION",
        "source_pins": PINS,
        "delivery": {"path": str(DEST / NAME), "bytes": len(payload), "sha256": PINS["collect_native_preflight.py"]},
        "command": COMMAND,
        "expected_report": "/home/cnooc/portal-ppt-native-preflight-20261017-r1.REPORT.json",
        "root_review": [
            "Read complete collector including final stdout-only emission and exact-byte stderr receipt",
            "Fixed GET resources and PostgreSQL-owner-derived node whitelist; no mutation/exec/SSH/CRI/database access",
            "No environment values, command text, secret/config contents or raw stderr are exported",
            "Resource projection handles Always-init/overhead; unsupported semantics do not produce capacity approval",
            "Before/after scope explicitly excludes full Gateway Pod identity rescan",
            "18-case receipt and final source/test hashes reconciled; real kubectl template coverage boundaries retained",
            "Independent read-only reviewer accepted the same final collector hash"
        ],
        "server_execution_performed": False, "ready_for_native_execution": False, "ready_for_rollout": False
    }
    with (SOURCE / "INDEPENDENT-REVIEW-DELIVERY.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(review, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"delivered": str(DEST / NAME), "bytes": len(payload), "sha256": PINS["collect_native_preflight.py"]}))


if __name__ == "__main__":
    main()
