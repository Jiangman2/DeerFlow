#!/usr/bin/env python3
"""Read-only, bounded host evidence collection for the 2026-09 initial audit.

Run locally on each Linux host. No SSH, secrets, password hashes, configuration
writes, service restarts, or automatic compliance claims. Output is internal.
"""
import argparse
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

COMMANDS = {
    "os": ["uname", "-srmo"],
    "packages": ["rpm", "-q", "nginx", "openssl", "openssh-server", "pam", "libpwquality", "nfs-utils"],
    "nginx_build": ["nginx", "-V"],
    "listeners": ["ss", "-lntu"],
    "ip_forward": ["sysctl", "net.ipv4.ip_forward"],
    "nfs_exports": ["exportfs", "-v"],
    "nfs_mounts": ["findmnt", "-t", "nfs,nfs4", "-o", "TARGET,SOURCE,FSTYPE"],
    "ssh_effective": ["sshd", "-T"],
}
SSH_FIELDS = {"permitrootlogin", "passwordauthentication", "pubkeyauthentication", "ciphers", "macs", "kexalgorithms", "authenticationmethods"}
POLICY_LINE = re.compile(r"^(?:minlen|minclass|[uldo]credit|enforce_for_root|enforcing|PASS_(?:MAX|MIN|WARN)_DAYS)\b|pam_(?:pwquality|cracklib)\.so")


def collect_command(argv, runner=subprocess.run):
    try:
        result = runner(argv, capture_output=True, text=True, errors="replace", timeout=15, check=False)
        output = result.stdout + result.stderr
        if argv[:2] == ["sshd", "-T"] and result.returncode == 0:
            output = "\n".join(line for line in output.splitlines() if line.split() and line.split()[0] in SSH_FIELDS)
        return {"command": argv, "status": "collected" if result.returncode == 0 else "error", "exit_code": result.returncode, "output": output[:24000], "truncated": len(output) > 24000}
    except FileNotFoundError:
        return {"command": argv, "status": "unavailable"}
    except subprocess.TimeoutExpired:
        return {"command": argv, "status": "timeout"}
    except OSError as exc:
        return {"command": argv, "status": "error", "error": str(exc)}


def collect_policy(root=Path("/")):
    paths = ["etc/security/pwquality.conf", "etc/pam.d/system-auth", "etc/pam.d/password-auth", "etc/login.defs"]
    dropins = root / "etc/security/pwquality.conf.d"
    discovery_error = None
    try:
        paths += [str(p.relative_to(root)) for p in sorted(dropins.glob("*.conf"))]
    except OSError as exc:
        discovery_error = str(exc)
    result = []
    if discovery_error:
        result.append({"path": str(dropins), "status": "error", "error": discovery_error})
    for name in paths:
        path = root / name
        try:
            lines = path.read_text(errors="replace").splitlines()
            selected = []
            for number, line in enumerate(lines, 1):
                line = line.split("#", 1)[0].strip()
                if line and POLICY_LINE.search(line):
                    selected.append({"line": number, "value": line})
            result.append({"path": name, "status": "collected", "settings": selected})
        except FileNotFoundError:
            result.append({"path": name, "status": "unavailable"})
        except OSError as exc:
            result.append({"path": name, "status": "error", "error": str(exc)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True, choices=["IP-A", "IP-B", "IP-C", "IP-D"], help="Use the report asset mapping confirmed by operations.")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Run on the target Linux host; this collector never connects remotely.")
    report = {"schema_version": 1, "asset": args.asset, "collected_at": datetime.now(timezone.utc).isoformat(), "status": "manual_review_required", "commands": {name: collect_command(argv) for name, argv in COMMANDS.items()}, "password_policy": collect_policy(), "limitations": ["Missing commands and permission errors are unknown, not compliant.", "sshd -T is the default context; audit every Match block separately.", "Host nginx may differ from edge, container, and sidecar nginx.", "PAM precedence and existing account passwords require manual verification.", "Listener inventory does not prove reachability or firewall enforcement.", "No CVE exploit, TLS handshake, or anonymous Metrics probe is performed."]}
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
