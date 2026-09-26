"""Amend only the immutable build1 OCI verifier; never assemble from HEAD."""

import argparse
import ast
import hashlib
import io
import json
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "deerflow-ppt-relay-tool-arguments-20260924-r1-build2"
BASE_NAME = "deerflow-ppt-relay-tool-arguments-20260924-r1-build1"
BASE_SHA = "843a97b3caf724da2ae979bfc1996c46820bfabbefac2f6120308d40187f18de"
OLD_GRAPH = """    layers = content.get("layers")
    diff_ids = config.get("rootfs", {}).get("diff_ids")
    if (
        not isinstance(layers, list)
        or not 1 <= len(layers) <= 128
        or config.get("rootfs", {}).get("type") != "layers"
        or not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or not all(DIGEST.fullmatch(item) for item in diff_ids)
    ):
        raise ValueError("Invalid image layer graph")
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_base(path):
    raw = path.read_bytes()
    if sha(raw) != BASE_SHA:
        raise ValueError("Immutable build1 archive checksum mismatch")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
        for member in bundle:
            if member.isdir() and member.name == BASE_NAME:
                continue
            prefix = BASE_NAME + "/"
            if (
                not member.isfile()
                or not member.name.startswith(prefix)
                or member.size > 2 * 1024**2
            ):
                raise ValueError("Unexpected build1 archive member")
            name = member.name[len(prefix) :]
            if "/" in name or name in files or name in (".", ".."):
                raise ValueError("Unexpected build1 archive path")
            files[name] = bundle.extractfile(member).read()
    manifest = dict(
        (name, digest)
        for digest, name in (
            row.split("  ", 1) for row in files["PACKAGE.SHA256"].decode().splitlines()
        )
    )
    if set(manifest) != set(files) - {"PACKAGE.SHA256"}:
        raise ValueError("Incomplete build1 manifest")
    for name, digest in manifest.items():
        if sha(files[name]) != digest:
            raise ValueError("Build1 file checksum mismatch")
    return files


def package(base, output):
    archive = output / (NAME + ".tar.gz")
    checksum = output / (NAME + ".tar.gz.sha256")
    if archive.exists() or checksum.exists():
        raise ValueError("Immutable build2 output already exists")
    files = read_base(base)
    original = files["verify_oci_base.py"]
    source = original.decode()
    if source.count(OLD_GRAPH) != 1 or source.count("import re\n") != 1:
        raise ValueError("Verifier anchor changed; refusing broad rewrite")
    source = source.replace(
        "import re\n", "import re\nfrom layer_graph import validate_layer_graph\n", 1
    )
    source = source.replace(
        OLD_GRAPH, "    layers = validate_layer_graph(content, config)\n", 1
    )
    files["verify_oci_base.py"] = source.encode()
    files["layer_graph.py"] = (
        (HERE / "layer_graph.py").read_text(encoding="utf-8").encode()
    )
    ast.parse(files["verify_oci_base.py"], feature_version=(3, 7))
    ast.parse(files["layer_graph.py"], feature_version=(3, 7))
    change = {
        "kind": "ppt-relay-build2-layer-graph-correction",
        "base_bundle_sha256": BASE_SHA,
        "original_verifier_sha256": sha(original),
        "corrected_verifier_sha256": sha(files["verify_oci_base.py"]),
        "layer_graph_sha256": sha(files["layer_graph.py"]),
        "observed_base_layer_count": 345,
        "observed_base_diff_id_count": 345,
        "layer_work_limit_before": 128,
        "layer_work_limit_after": 1024,
        "index_fanout_limit": 128,
        "metadata_byte_limit": 16 * 1024**2,
        "per_layer_byte_limit": 32 * 1024**3,
        "total_layer_byte_limit": 64 * 1024**3,
        "layer_blobs_all_hashed": True,
        "application_payload_changed_from_build1": False,
        "base_and_runtime_configuration_changed": False,
        "production_rollout_ready": False,
    }
    files["BUILD2-CHANGES.json"] = (json.dumps(change, indent=2) + "\n").encode()
    intro = """# Build2：345 层基线兼容修正

仅修正构建侧 OCI 层结构校验：工作上限从 128 层调整到 1024 层，并输出
具体的结构错误。镜像索引扇出上限仍为 128；镜像名、运行镜像 ID、架构、
元数据 SHA256、每个压缩层的大小和 SHA256、总字节上限全部保留。
层数格式通过不是完整性通过；本次构建会重新进行完整验证。

应用代码、Dockerfile、基线锁、构建控制器、隔离/资源限制及离线策略与
已交付 Build1 逐字节相同。不修改线上 Deployment、模型、数据库或沙箱。
BUILD-INFRA-PROVENANCE.json 保留原始来源；此次增量见 BUILD2-CHANGES.json。

请解压至独立 Build2 包目录，使用新的 Build2 输出目录；保留原 Build1
失败目录供诊断，不覆盖或复用其部分结果。无需重传或重新导出基线镜像。

"""
    files["BUILD-README.md"] = intro.encode() + files["BUILD-README.md"].replace(
        b"ppt-relay-r1-build1", b"ppt-relay-r1-build2"
    )
    files["BUILD-VERIFICATION.md"] += (
        (HERE / "BUILD2-VERIFICATION.md").read_text(encoding="utf-8").encode()
    )
    files["PACKAGE.SHA256"] = "".join(
        sha(raw) + "  " + name + "\n"
        for name, raw in sorted(files.items())
        if name != "PACKAGE.SHA256"
    ).encode("ascii")
    with (
        archive.open("xb") as stream,
        tarfile.open(fileobj=stream, mode="w:gz") as bundle,
    ):
        directory = tarfile.TarInfo(NAME)
        directory.type, directory.mode = tarfile.DIRTYPE, 0o755
        bundle.addfile(directory)
        for name, raw in sorted(files.items()):
            info = tarfile.TarInfo(NAME + "/" + name)
            info.size, info.mode = len(raw), 0o644
            bundle.addfile(info, io.BytesIO(raw))
    with checksum.open("x", encoding="ascii", newline="\n") as stream:
        stream.write(sha(archive.read_bytes()) + "  " + archive.name + "\n")
    print("ppt_relay_build2_package=" + str(archive))
    print("production_rollout_ready=no")
    return archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.base, args.output)
