"""Internal fixed-provider collector. Mount credentials only in this dedicated process.

The shared Skill stays byte-identical. Only dated market cache files are exposed.
No public ingress, arbitrary URL, user document, model or Work operation is supported.
"""

import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SKILL = Path(os.environ.get("ENERGY_ORIGINAL_SKILL", "/opt/energy-skill"))
PRIVATE = Path(os.environ.get("ENERGY_PRIVATE_ROOT", "/private"))
FEED = Path(os.environ.get("ENERGY_MARKET_FEED", "/feed"))
CREDENTIAL = Path("/run/energy-credential/credential.json")
HOST, PORT = "ip91.jkx.com.cn", 6688
HTTP_PROXY = "http://10.68.211.1:8080"
TZ = dt.timezone(dt.timedelta(hours=8))
LOCK = threading.Lock()
STATE = {}
ACTIVE = None

# Network admission belongs to the process wrapper, not to a modified supplier module.
RUNNER = r"""
import contextlib,datetime as dt,io,json,os,socket,sys
endpoint='http://ip91.jkx.com.cn:6688/jkx_eecc.aspx'
proxy='http://10.68.211.1:8080'; proxy_host='10.68.211.1'; proxy_port=8080
out={'status':'INCOMPLETE','connections':0}
stage='validate_budget'
try:
 connection_budget=int(sys.argv[3])
 if not 1<=connection_budget<=188:raise ValueError('NETWORK_BUDGET_INVALID')
 stage='validate_proxy'
 if os.environ.get('ENERGY_MARKET_HTTP_PROXY')!=proxy:raise ValueError('FIXED_PROXY_REQUIRED')
 def guard(event,args):
  if event=='socket.getaddrinfo':
   name=args[0].decode('ascii') if isinstance(args[0],bytes) else args[0]
   if name!=proxy_host or args[1] not in (proxy_port,str(proxy_port)):raise RuntimeError('NETWORK_TARGET_REFUSED')
  elif event=='socket.connect':
   address=args[1]
   if not isinstance(address,tuple) or len(address)<2 or address[0]!=proxy_host or address[1]!=proxy_port:raise RuntimeError('NETWORK_TARGET_REFUSED')
   if out['connections']>=connection_budget:raise RuntimeError('NETWORK_BUDGET')
   out['connections']+=1
 sys.addaudithook(guard)
 stage='original_import'
 sys.path.insert(0,sys.argv[1])
 import fetch_market_history as original
 sys.argv=['fetch_market_history.py','--date',sys.argv[2]]
 stage='prepare_fixed_transport'
 session=original.requests.Session()
 session.trust_env=False
 session.proxies={'http':proxy}
 session.mount('http://',original.requests.adapters.HTTPAdapter(max_retries=0))
 # Requests otherwise prepares _next and consumes redirect bodies even with
 # allow_redirects=False. No redirect parsing, body pre-read or follow-up is allowed.
 session.resolve_redirects=lambda *args,**kwargs: iter(())
 def fixed_get(url,**kwargs):
  if url!=endpoint or set(kwargs)!={'params','timeout'}:raise ValueError('PROVIDER_REQUEST_SHAPE')
  params=kwargs['params']
  if not isinstance(params,dict) or set(params)!={'p','pdate'}:raise ValueError('PROVIDER_REQUEST_SHAPE')
  if not params['p'] or params['p']!=os.environ.get('JKX_API_PASSWORD'):raise ValueError('PROVIDER_REQUEST_SHAPE')
  day=params['pdate']
  if not isinstance(day,str) or dt.date.fromisoformat(day).isoformat()!=day or kwargs['timeout']!=30:raise ValueError('PROVIDER_REQUEST_SHAPE')
  response=None
  try:
   session.cookies.clear()
   response=session.get(url,params=params,timeout=kwargs['timeout'],allow_redirects=False,stream=True,proxies={'http':proxy})
   if 300<=response.status_code<400:raise ValueError('PROVIDER_REDIRECT_REFUSED')
   chunks=[]; size=0
   for chunk in response.iter_content(chunk_size=16384):
    size+=len(chunk)
    if size>262144:raise ValueError('PROVIDER_RESPONSE_BOUND')
    chunks.append(chunk)
   response._content=b''.join(chunks)
   response._content_consumed=True
   return response
  finally:
   if response is not None:response.close()
   session.cookies.clear()
 original_get=original.requests.get
 try:
  original.requests.get=fixed_get
  stage='original_fetch'
  with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):original.main()
 finally:
  original.requests.get=original_get
  session.close()
 out['status']='ORIGINAL_MARKET_COMPLETE'
except Exception as exc:
 out['error_class']=type(exc).__name__
 out['failure_stage']=stage
 detail=str(exc)
 if isinstance(exc,ValueError) and stage=='original_fetch' and detail.endswith(': target date has no quote'):
  out['error_code']='TARGET_QUOTES_UNAVAILABLE'
 elif isinstance(exc,ValueError) and detail.endswith(': API request failed (endpoint redacted)'):
  out['error_code']='PROVIDER_REQUEST_FAILED'
 elif detail in ('NETWORK_TARGET_REFUSED','NETWORK_BUDGET','NETWORK_BUDGET_INVALID','FIXED_PROXY_REQUIRED'):
  out['error_code']=detail
 else:out['error_code']='ORIGINAL_COLLECTION_FAILED'
print(json.dumps(out,sort_keys=True))
"""


def configured_proxy():
    value = os.environ.get("ENERGY_MARKET_HTTP_PROXY")
    if value != HTTP_PROXY:
        raise ValueError("FIXED_PROXY_REQUIRED")
    return value


def load_entry():
    spec = importlib.util.spec_from_file_location(
        "energy_entry", SKILL / "scripts/entry.py"
    )
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    entry.original_files()
    return entry


def credential():
    # Kubernetes Secret projection uses ..data symlinks; resolve only inside its fixed mount.
    actual = CREDENTIAL.resolve(strict=True)
    if Path("/run/energy-credential") not in actual.parents:
        raise ValueError("CREDENTIAL_MOUNT_ESCAPE")
    st = actual.stat()
    if st.st_size > 4096 or st.st_mode & 0o077:
        raise ValueError("CREDENTIAL_MODE")
    value = json.loads(actual.read_bytes())
    if (
        set(value) != {"JKX_API_ENDPOINT", "JKX_API_PASSWORD"}
        or value["JKX_API_ENDPOINT"] != "http://ip91.jkx.com.cn:6688/jkx_eecc.aspx"
    ):
        raise ValueError("CREDENTIAL_SCHEMA")
    if (
        not isinstance(value["JKX_API_PASSWORD"], str)
        or not 1 <= len(value["JKX_API_PASSWORD"]) <= 1024
    ):
        raise ValueError("CREDENTIAL_VALUE")
    return value


def date_window(day):
    parsed = dt.date.fromisoformat(day)
    today = dt.datetime.now(TZ).date()
    if parsed.isoformat() != day or not today - dt.timedelta(days=7) <= parsed <= today:
        raise ValueError("DATE_WINDOW")
    return parsed


def publish_cache(engine, key, entry, target_day):
    # Fixed response fields only. Prove the original normalizer sees identical observations.
    spec = importlib.util.spec_from_file_location(
        "energy_original_fetch", engine / "app/fetch_market_history.py"
    )
    prior_path = list(sys.path)
    try:
        sys.path.insert(0, str(engine / "app"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path in sorted((engine / "app/sources").glob("jkx_new_????-??-??.json")):
            day = path.stem.removeprefix("jkx_new_")
            raw = entry.read(path, 262144)
            payload = json.loads(raw)
            original = module.normalize(payload, day)
            # A target with no quotes must be fetched again after the cooldown,
            # not permanently frozen by the upstream cache-first behavior.
            required_codes = set(module.SPECS) | set(module.FREIGHT)
            if day == target_day and any(
                r["status"] != "available" for r in original
                if r["code"] in required_codes
            ):
                continue
            public = {
                "errcode": 0,
                "data": [
                    {
                        k: r[k]
                        for k in ("code_name", "value_date", "close", "unit", "item")
                    }
                    for r in payload["data"]
                ],
            }
            if module.normalize(public, day) != original:
                raise ValueError("CACHE_PROJECTION_CHANGED_OBSERVATIONS")
            encoded = (
                json.dumps(public, ensure_ascii=False, allow_nan=False) + "\n"
            ).encode("utf-8")
            if key.encode("utf-8") in encoded or len(encoded) > 262144:
                raise ValueError("CACHE_PRIVATE_OUTPUT_REJECTED")
            target = FEED / path.name
            if target.exists():
                if entry.read(target, 262144) != encoded:
                    raise ValueError("PUBLISHED_CACHE_CONFLICT")
                continue
            fd, name = tempfile.mkstemp(prefix=".stage-", dir=FEED)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(name, 0o644)
                os.link(name, target)
            finally:
                os.unlink(name)
    finally:
        sys.path[:] = prior_path


def collect(day):
    global ACTIVE
    result = {"status": "INCOMPLETE", "data_date": day, "credentials_exported": False}
    try:
        proxy = configured_proxy()
        entry = load_entry()
        key = credential()
        with tempfile.TemporaryDirectory(prefix="original-", dir=PRIVATE) as work:
            engine = Path(work) / "engine"
            shutil.copytree(SKILL / "upstream", engine)
            expected = entry.original_files()
            entry.verify_tree(engine, expected, False)
            for path in FEED.glob("jkx_new_????-??-??.json"):
                if not re.fullmatch(r"jkx_new_\d{4}-\d{2}-\d{2}\.json", path.name):
                    raise ValueError("CACHE_FILENAME")
                (engine / "app/sources" / path.name).write_bytes(
                    entry.read(path, 262144)
                )
            env = {
                k: os.environ[k]
                for k in ("PATH", "HOME", "LANG", "LD_LIBRARY_PATH")
                if k in os.environ
            }
            env.update(key)
            env.update(
                ENERGY_MARKET_HTTP_PROXY=proxy,
                PYTHONUTF8="1",
                PYTHONDONTWRITEBYTECODE="1",
            )
            with tempfile.TemporaryFile() as log:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-B",
                        "-c",
                        RUNNER,
                        str(engine / "app"),
                        day,
                        "188",
                    ],
                    env=env,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    child.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
                    raise ValueError("COLLECTOR_TIMEOUT") from None
                if child.returncode != 0 or log.tell() > 8192:
                    raise ValueError("COLLECTOR_PROCESS_FAILED")
                log.seek(0)
                observation = json.loads(log.read())
            entry.verify_tree(engine, expected, True)
            result["target_response_validated"] = (
                engine / "app/sources" / ("jkx_new_" + day + ".json")
            ).is_file()
            # Retain verified historical cache even if the target day is not ready.
            publish_cache(engine, key["JKX_API_PASSWORD"], entry, day)
            result["connections"] = observation.get("connections")
            if observation.get("status") == "ORIGINAL_MARKET_COMPLETE":
                result["status"] = "READY"
            elif (
                result["target_response_validated"]
                and observation.get("error_code") == "TARGET_QUOTES_UNAVAILABLE"
            ):
                result["status"] = "WAIT_DATA"
            else:
                result["error_code"] = observation.get(
                    "error_code", "ORIGINAL_COLLECTION_FAILED"
                )
                result["failure_stage"] = observation.get(
                    "failure_stage", "original_fetch"
                )
                result["error_class"] = observation.get("error_class", "Unknown")
        entry.original_files()
    except Exception as exc:
        result["error_class"] = type(exc).__name__
    finally:
        with LOCK:
            STATE[day] = dict(result, retry_after_epoch=time.time() + 300)
            ACTIVE = None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass

    def reply(self, code, body):
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "UP"})
        match = re.fullmatch(r"/status/(\d{4}-\d{2}-\d{2})", self.path)
        if match:
            day = match.group(1)
            try:
                date_window(day)
            except ValueError:
                return self.reply(400, {"status": "DATE_WINDOW"})
            with LOCK:
                if ACTIVE == day:
                    result = {"status": "COLLECTING", "data_date": day}
                elif day in STATE:
                    result = {
                        k: v for k, v in STATE[day].items()
                        if k != "retry_after_epoch"
                    }
                else:
                    result = {"status": "NOT_REQUESTED", "data_date": day}
                    if ACTIVE is not None:
                        result["active_data_date"] = ACTIVE
            return self.reply(200, result)
        return self.reply(404, {"status": "NOT_FOUND"})

    def do_POST(self):
        global ACTIVE
        match = re.fullmatch(r"/prepare/(\d{4}-\d{2}-\d{2})", self.path)
        if (
            not match
            or self.headers.get("Transfer-Encoding")
            or self.headers.get("Content-Length", "0") != "0"
        ):
            return self.reply(400, {"status": "INVALID_REQUEST"})
        day = match.group(1)
        try:
            date_window(day)
        except ValueError:
            return self.reply(400, {"status": "DATE_WINDOW"})
        with LOCK:
            for old in list(STATE):
                if (
                    old
                    < (dt.datetime.now(TZ).date() - dt.timedelta(days=7)).isoformat()
                ):
                    STATE.pop(old, None)
            current = STATE.get(day)
            if current and (
                current["status"] == "READY"
                or current["retry_after_epoch"] > time.time()
            ):
                return self.reply(
                    200 if current["status"] == "READY" else 202,
                    {k: v for k, v in current.items() if k != "retry_after_epoch"},
                )
            if ACTIVE is not None:
                return self.reply(202, {"status": "COLLECTING", "data_date": day})
            ACTIVE = day
            threading.Thread(target=collect, args=(day,), daemon=True).start()
        return self.reply(202, {"status": "COLLECTING", "data_date": day})


class BoundedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    slots = threading.BoundedSemaphore(16)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def main():
    configured_proxy()
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    FEED.mkdir(parents=True, exist_ok=True)
    if PRIVATE.is_symlink() or FEED.is_symlink():
        raise ValueError("MOUNT_SYMLINK")
    load_entry()
    credential()
    server = BoundedServer(("0.0.0.0", 8766), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
