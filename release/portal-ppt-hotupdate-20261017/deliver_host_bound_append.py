"""Verify reviewed fixed bytes and deliver the small one-shot server script."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parent.parent.parent / "portal-ppt-merge-20261017/host-oci-overlay-bound-r1"
DESTINATION = Path("C:/Users/15285/Downloads/portal-ppt-host-oci-append-20261017-r1")
SCRIPT = "run-portal-ppt-host-oci-append-20261017-r1.sh"
PINS = {
    SCRIPT: "c874898b2ed9ebae307e7c7b975a64e3f20e539fb163bbdd7456c84b9ba533c8",
    "bound_append.py": "cca326b141f733dd4fc95575acdef2ee171263a84becd98e6d61bd3bc61c925f",
    "wrapper_bootstrap.py": "3c709a90fef6100a2fe9e4b090fb1046d93eb62d1dcff630594c62d423a0d656",
    "host_oci_append.py": "f40ec6e0997608845d22e58f3e54e578d5ae82785c5f723dc8c39c7f2d958700",
    "generate_wrapper.py": "9635f5e6c82ca698715f065c46cb3043a9d8a425044d58a80155a8de60642525",
    "PREPARATION.json": "6431ac61ea52ae7fe4acd65d17360da5ce37eeb7998f1d45600e4f29454db31e",
    "qualification/linux-r3/RECEIPT.json": "04ae9a05078fd4dccea1b7bf8dfb290f3a54eddf1c50c612b579d448c751689a",
    "evidence/REPORT.json": "27f3658cb5933bf1a7f5f4b3b0173ff761b2ab9c60fe6f3db42c936167da1048",
    "evidence/INDEPENDENT-REVIEW.json": "82f478d2c96f9316b1ebdb56dcfe68ca7bf82a1f259198fa38001386819e823d",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def main():
    for name, expected in PINS.items():
        assert sha((PACKAGE / name).read_bytes()) == expected, name
    receipt = json.loads((PACKAGE / "qualification/linux-r3/RECEIPT.json").read_bytes())
    assert receipt["status"] == "PASS" and receipt["returncode"] == 0
    assert receipt["real_archive"] is False and receipt["network"] is False
    assert receipt["cleanup"]["status"] == "REMOVED"
    for name, row in receipt["sources"].items():
        raw = (PACKAGE / name).read_bytes()
        assert len(raw) == row["bytes"] and sha(raw) == row["sha256"], name
    log = (PACKAGE / "qualification/linux-r3/stderr.log").read_bytes()
    assert sha(log) == receipt["stderr_sha256"]
    assert b"Ran 14 tests" in log and log.rstrip().endswith(b"OK")
    spec = importlib.util.spec_from_file_location("reviewed_host_delivery_generator", PACKAGE / "generate_wrapper.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    generated, expected_files = generator.materialize()
    assert len(expected_files) == 20
    assert generated == (PACKAGE / SCRIPT).read_bytes() and len(generated) == 263861
    assert sha(generated) == PINS[SCRIPT]
    DESTINATION.mkdir(exist_ok=False)
    write_new(DESTINATION / SCRIPT, generated)
    assert sha((DESTINATION / SCRIPT).read_bytes()) == PINS[SCRIPT]
    instructions = """PPT Host 候选镜像生成（尚不是生产热更）

上传 run-portal-ppt-host-oci-append-20261017-r1.sh 到 datax-01 的 /home/cnooc/。
然后执行这一整行：
sudo bash /home/cnooc/run-portal-ppt-host-oci-append-20261017-r1.sh

脚本绑定已审阅的现存5337486336字节基底，追加4项替换和8项新文件。
仅生成新的私有OCI及报告；不调用Kubernetes、containerd，不运行镜像，不安装依赖。
原归档保留；不改/home/cnooc权限，所有root写完后才移交新目录给cnooc。
需约9.65GB可用空间（包含4GiB余量），脚本会检查，不够时不自动删除内容。
完成后贴回终端摘要，并下载download_report指向的REPORT.json给本任务。
新OCI留在服务器，不用下载到Windows。失败或中断后保留目录，先不要重跑。

此操作通过后仍需原生ARM64运行验收：默认Chromium启动、Codex可执行文件、
PPT确认及产物流程。它不是ready_for_rollout证明。
"""
    write_new(DESTINATION / "README.txt", instructions.encode("utf-8"))
    write_new(DESTINATION / "SHA256SUMS.txt", (PINS[SCRIPT] + "  " + SCRIPT + "\n").encode("ascii"))
    review = {
        "schema": "portal-ppt-host-bound-append-parent-review-delivery.v1",
        "status": "REVIEWED_AND_DELIVERED_FOR_FIXED_OFFLINE_HOST_BUILD_ONLY",
        "pinned_inputs": PINS,
        "root_review": [
            "Read full child, wrapper, generator and interruption cleanup revision",
            "Exact base/report/review/payload/header hashes bound; no arbitrary profile or archive CLI",
            "New root-private control/output, disk and memory limits, bounded child process and output",
            "Exact four-file FD/hash/size/identity verification before ownership transfer; directory transferred last",
            "Parent interrupt, thread-start failure and timeout terminate only owned process group",
            "No persistent root writes after handoff, no home permission changes, no K8s/containerd/network/image execution",
            "Linux 14-case receipt and all tested source/log hashes independently reconciled",
            "Generated upload script independently reproduced byte-for-byte, including all 20 embedded files",
        ],
        "linux_tests_passed": 14,
        "delivery": {"path": str(DESTINATION / SCRIPT), "bytes": len(generated), "sha256": PINS[SCRIPT]},
        "server_command": "sudo bash /home/cnooc/" + SCRIPT,
        "server_execution_performed": False,
        "real_host_candidate_built": False,
        "native_runtime_qualified": False,
        "ready_for_rollout": False,
    }
    write_new(PACKAGE / "INDEPENDENT-REVIEW-DELIVERY.json", (json.dumps(review, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"status": review["status"], "delivery": review["delivery"],
                      "review_sha256": sha((PACKAGE / "INDEPENDENT-REVIEW-DELIVERY.json").read_bytes())}))


if __name__ == "__main__":
    main()
