"""Run in one Gateway container. Exercise the installed route with release-owned storage."""
import hashlib
import json
import sys
import time
from uuid import UUID
from urllib.parse import urlsplit
from pathlib import Path

import requests
from deerflow.config import get_app_config
from deerflow.config.paths import get_paths


def main():
    user, thread, nonce = sys.argv[1:4]
    if any(str(UUID(x)) != x for x in (user, thread, nonce)):
        raise ValueError("Invalid commissioning identity")
    sid = hashlib.sha256(f"{user}:{thread}".encode()).hexdigest()[:8]
    paths = get_paths()
    base = paths.user_dir(user)
    marker = base / ".df-pool-commission"
    if sys.argv[4:] == ["--cleanup"]:
        if base.exists():
            if (base.parent != paths.base_dir / "users" or base.name != user or base.is_symlink()
                    or marker.is_symlink() or marker.read_text() != nonce):
                raise RuntimeError("Refusing to clean unowned commissioning directory")
            import shutil
            if not shutil.rmtree.avoids_symlink_attacks:
                raise RuntimeError("Safe directory cleanup unavailable")
            shutil.rmtree(base)
        print(json.dumps({"stage": "COMMISSION_STORAGE_CLEANED"}))
        return
    if not base.exists():
        base.mkdir()
        marker.write_text(nonce)
    if base.is_symlink() or marker.is_symlink() or marker.read_text() != nonce:
        raise RuntimeError("Commissioning directory collision")
    paths.ensure_thread_dirs(thread, user_id=user)
    custom = paths.user_custom_skills_dir(user)
    custom.mkdir(parents=True, exist_ok=True)
    token_file = custom / "df-commission.txt"
    token_file.write_text(nonce)
    token_file.chmod(0o644)
    cfg = get_app_config().sandbox
    url = cfg.provisioner_url.rstrip("/") + "/api/sandboxes"
    payload = dict(user_id=user, thread_id=thread, sandbox_id=sid, include_legacy_skills=False)
    s = requests.Session()
    s.trust_env = False
    headers = {"X-API-Key": cfg.provisioner_api_key}
    since = time.monotonic()
    r = s.post(url, json=payload, headers=headers, timeout=15, allow_redirects=False)
    while r.status_code == 425 and time.monotonic() - since < 178:
        time.sleep(1)
        r = s.post(url + "/lookup", json=payload, headers=headers, timeout=12, allow_redirects=False)
    r.raise_for_status()
    binding = r.json()["sandbox_url"]
    peer = urlsplit(binding)
    if not peer.hostname or not peer.hostname.startswith("df-w-") or not peer.hostname.endswith(".deer-flow.svc.cluster.local"):
        raise RuntimeError("First allocation missed the pool")
    # Only the uniquely named commissioning account can see this input marker.
    command = (f"test \"$(cat /mnt/skills/custom/df-commission.txt)\" = '{nonce}' && "
               f"printf '%s' '{nonce}' > /mnt/user-data/outputs/df-commission.txt && echo DF_RW_OK")
    for attempt in range(8):
        try:
            r = s.post(binding + "/v1/shell/exec", headers={"Authorization": "Bearer " + cfg.sandbox_api_key},
                       json={"command": command}, timeout=(3, 12), allow_redirects=False)
            break
        except (requests.ConnectTimeout, requests.ConnectionError):
            if attempt == 7:
                raise
            time.sleep(1)
    r.raise_for_status()
    data = r.json().get("data") or {}
    output = paths.sandbox_outputs_dir(thread, user_id=user) / "df-commission.txt"
    if data.get("exit_code") != 0 or "DF_RW_OK" not in (data.get("output") or "") or output.read_text() != nonce:
        raise RuntimeError("First assignment failed file round-trip")
    with s.get(f"http://{peer.hostname}:8788/health", timeout=8, allow_redirects=False) as ppt:
        if ppt.status_code != 200:
            raise RuntimeError("PPT host failed health check")
    elapsed = round(time.monotonic() - since, 3)
    r = s.delete(url + "/" + sid, headers=headers, params={"binding_url": binding}, timeout=15)
    r.raise_for_status()
    print(json.dumps({"stage": "FIRST_GLOBAL_BINDING_VERIFIED", "create_to_rw_and_ppt_seconds": elapsed,
                      "release_requested": True}))


if __name__ == "__main__":
    main()
