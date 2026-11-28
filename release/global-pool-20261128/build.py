"""Rebuild the installed R4 zipapp, whose content hash is its deployment identity."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent
SHA256 = "35f398710736da6311c10772ee0d8b47d4e8daa4837b1d9ef0e1f21d272effbe"


def build():
    source = ROOT / "r4"
    layout = json.loads((ROOT / "zip_layout.json").read_text())
    files = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    assert files == {name for name, _, _ in layout}
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, timestamp, attributes in layout:
            payload = (source / name).read_bytes()
            if name.endswith(".py"):
                compile(payload, name, "exec")
            info = ZipInfo(name, tuple(timestamp))
            info.create_system = 0
            info.external_attr = attributes
            archive.writestr(info, payload, compress_type=ZIP_DEFLATED)
    data = output.getvalue()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Rebuilt archive differs from installed R4")
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = build()
    if args.output:
        args.output.write_bytes(data)
    print("Verified installed R4 SHA-256", SHA256)
