"""Temporary internal HTTPS receiver. Auth before body; no download endpoint."""

import argparse
import email.policy
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import socketserver
import ssl
import tempfile
import threading
import time
import uuid
import zipfile
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MAX_BODY = 6 * 1024 * 1024
LOCK = threading.Lock()


def parse_body(content_type, body):
    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("multipart required")
    if "\r" in content_type or "\n" in content_type or len(content_type) > 512:
        raise ValueError("invalid header")
    msg = BytesParser(policy=email.policy.default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode("ascii") + body)
    if not msg.is_multipart() or msg.defects:
        raise ValueError("invalid multipart")
    fields = {}
    report = None
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if part.get_content_disposition() != "form-data" or name not in {"request_id", "job_id", "status", "message", "report"} or name in fields or (name == "report" and report is not None):
            raise ValueError("invalid fields")
        data = part.get_payload(decode=True)
        if data is None or part.is_multipart() or part.defects:
            raise ValueError("invalid part")
        if name == "report":
            if len(data) > 5 * 1024 * 1024:
                raise ValueError("report too large")
            if part.get_content_type() == "text/markdown":
                value = data.decode("utf-8-sig")
                if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value) or not (part.get_filename() or "").endswith(".md"):
                    raise ValueError("invalid markdown")
            else:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    info = z.infolist()
                    if len(info) > 1024 or sum(x.file_size for x in info) > 40 * 1024 * 1024 or "word/document.xml" not in z.namelist():
                        raise ValueError("invalid docx")
            report = data
        else:
            if len(data) > 4096 or part.get_filename() is not None:
                raise ValueError("invalid field")
            fields[name] = data.decode("utf-8")
    if set(fields) != {"request_id", "job_id", "status", "message"}:
        raise ValueError("missing fields")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", fields["request_id"]):
        raise ValueError("invalid request id")
    if str(uuid.UUID(fields["job_id"])) != fields["job_id"]:
        raise ValueError("invalid job id")
    if fields["status"] not in {"succeeded", "failed"} or (fields["status"] == "succeeded") != (report is not None):
        raise ValueError("status/report mismatch")
    return fields, report


def save_delivery(root, fields, report):
    proof = {**fields, "report_sha256": hashlib.sha256(report).hexdigest() if report else None, "report_bytes": len(report) if report else 0}
    if report is not None and not report.startswith(b"PK\x03\x04"):
        proof["report_format"] = "md"
    fingerprint = hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    dest = root / fields["job_id"]
    with LOCK:
        if dest.exists():
            if dest.is_symlink() or json.loads((dest / "receipt.json").read_bytes())["fingerprint"] != fingerprint:
                return 409
            return 200
        if sum(1 for _ in root.iterdir()) >= 1000 or shutil.disk_usage(root).free < 512 * 1024 * 1024:
            return 507
        temp = Path(tempfile.mkdtemp(prefix=".incoming-", dir=root))
        try:
            if report is not None:
                (temp / ("report.md" if proof.get("report_format") == "md" else "report.docx")).write_bytes(report)
            (temp / "receipt.json").write_text(json.dumps({**proof, "fingerprint": fingerprint, "received_at": time.time()}, ensure_ascii=False, indent=2), encoding="utf-8")
            for f in temp.iterdir():
                f.chmod(0o600)
                with f.open("rb+") as stream:
                    os.fsync(stream.fileno())
            os.rename(temp, dest)
            if os.name == "posix":
                fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if temp.exists():
                shutil.rmtree(temp)
    return 200


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def get_request(self):
        raw, address = self.socket.accept()
        raw.settimeout(10)
        try:
            return self.tls.wrap_socket(raw, server_side=True), address
        except BaseException:
            raw.close()
            raise


class Handler(BaseHTTPRequestHandler):
    server_version = "ProcurementTestCallback/1"
    sys_version = ""

    def log_message(self, *args):
        pass

    def reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_GET(self):
        self.reply(200 if self.path == "/health" else 404)

    def do_POST(self):
        if self.path != "/procurement-review/callback":
            self.reply(404)
            return
        values = self.headers.get_all("Authorization", [])
        if len(values) != 1 or not hmac.compare_digest(values[0].encode(), ("Bearer " + self.server.key).encode()):
            self.reply(401)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get("Transfer-Encoding"):
            self.reply(411)
            return
        size = int(lengths[0])
        if not 0 < size <= MAX_BODY:
            self.reply(413)
            return
        try:
            deadline = time.monotonic() + 25
            data = bytearray()
            while len(data) < size:
                if time.monotonic() > deadline:
                    raise TimeoutError()
                block = self.rfile.read(min(65536, size - len(data)))
                if not block:
                    raise ValueError("incomplete body")
                data.extend(block)
            fields, report = parse_body(self.headers.get("Content-Type", ""), bytes(data))
            status = save_delivery(self.server.storage, fields, report)
        except (ValueError, UnicodeError, zipfile.BadZipFile):
            status = 422
        except (TimeoutError, OSError):
            status = 503
        self.reply(status)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bind", required=True)
    p.add_argument("--port", type=int, default=28443)
    p.add_argument("--root", type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    key = (args.root / "callback.key").read_text().strip()
    if len(key) < 40:
        raise RuntimeError("missing key")
    storage = args.root / "reports"
    storage.mkdir(mode=0o700, exist_ok=True)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(args.root / "server.crt", args.root / "server.key")
    server = Server((args.bind, args.port), Handler)
    server.tls = tls
    server.key = key
    server.storage = storage
    server.serve_forever()


if __name__ == "__main__":
    main()
