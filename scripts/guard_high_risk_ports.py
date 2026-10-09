#!/usr/bin/env python3
"""Guard selected node ports using a dedicated nftables table; audit by default.
Python 3.6+, Linux nftables and systemd are required for apply.
"""
import argparse
import contextlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

TABLE = "deerflow_port_guard"
STATE_ROOT = Path("/var/lib/deerflow-port-guard")


def command(argv, check=True):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError("{}: {}".format(argv[0], result.stderr.strip()))
    return result


def validate(policy):
    if not isinstance(policy, dict):
        raise ValueError("Policy must be a JSON object.")
    if policy.get("reviewed") is not True:
        raise ValueError("Set reviewed=true only after reviewing node/CNI/NFS/SSH dependencies.")
    interfaces = policy.get("interfaces", [])
    if not isinstance(interfaces, list) or not interfaces or any(
            not isinstance(x, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", x) for x in interfaces):
        raise ValueError("Provide real inbound interface names, e.g. eth0; no wildcards.")
    services = policy.get("services", [])
    if not isinstance(services, list) or not services:
        raise ValueError("No services configured.")
    seen = set()
    ssh = None
    for service in services:
        if not isinstance(service, dict):
            raise ValueError("Each service must be a JSON object.")
        name = service.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", name):
            raise ValueError("Invalid service name.")
        proto = service.get("protocol")
        if proto not in ("tcp", "udp"):
            raise ValueError("protocol must be tcp or udp.")
        ports = service.get("ports", [])
        if not isinstance(ports, list) or not ports or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
            raise ValueError("ports must be integers 1..65535.")
        for port in ports:
            if (proto, port) in seen:
                raise ValueError("Duplicate port/protocol: {} {}".format(proto, port))
            seen.add((proto, port))
        sources = service.get("sources", [])
        if not isinstance(sources, list):
            raise ValueError("sources must be a list of IP addresses/CIDRs.")
        for source in sources:
            if not isinstance(source, str):
                raise ValueError("sources must contain IP/CIDR strings.")
            net = ipaddress.ip_network(source, strict=True)
            if net.prefixlen == 0:
                raise ValueError("Unrestricted /0 sources are not allowed.")
        if service["name"] == "ssh":
            if proto != "tcp" or len(ports) != 1 or not sources:
                raise ValueError("ssh needs one actual TCP port and explicit admin sources.")
            ssh = service
    if ssh is None:
        raise ValueError("Include the ssh service with its actual listening port.")
    return policy


def render(policy, owner=None):
    validate(policy)
    lines = ["table inet " + TABLE + " {", "  chain ingress_guard {",
             "    type filter hook prerouting priority -150; policy accept;",
             "    iifname != { " + ", ".join(json.dumps(x) for x in policy["interfaces"]) + " } return",
             "    fib daddr type != local return"]
    if owner:
        if not re.fullmatch(r"[0-9]+-[0-9]+", owner):
            raise ValueError("Invalid owner marker.")
        lines.insert(3, '    iifname "lo" return comment "deerflow-owner-{}"'.format(owner))
    for service in policy["services"]:
        ports = ", ".join(str(p) for p in service["ports"])
        for source in service["sources"]:
            net = ipaddress.ip_network(source, strict=True)
            family = "ip" if net.version == 4 else "ip6"
            lines.append("    {} saddr {} {} dport {{ {} }} counter return".format(
                family, net, service["protocol"], ports))
        lines.append('    {} dport {{ {} }} counter drop comment "{}"'.format(
            service["protocol"], ports, service["name"]))
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def check_ssh(policy, console_access=False):
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if connection:
        if len(connection) != 4:
            raise ValueError("Invalid SSH_CONNECTION.")
        client = ipaddress.ip_address(connection[0])
        ssh = next(s for s in policy["services"] if s["name"] == "ssh")
        if int(connection[3]) != ssh["ports"][0]:
            raise ValueError("Configured ssh port differs from current SSH connection.")
        if not any(client in ipaddress.ip_network(s) for s in ssh["sources"]):
            raise ValueError("Current SSH client is outside the ssh allowlist.")
    else:
        if not console_access:
            raise ValueError("SSH_CONNECTION missing: preserve it with sudo --preserve-env=SSH_CONNECTION, or use --console-access from a verified console.")


def require_root():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("This operation requires root on Linux.")


@contextlib.contextmanager
def locked():
    import fcntl
    require_root()
    with open("/run/deerflow-port-guard.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def save(state, data):
    tmp = state / "state.tmp"
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(state / "state.json"))


def read_state(path):
    original = Path(path)
    state = original.resolve()
    if original.is_symlink() or state.parent != STATE_ROOT or state.stat().st_uid != 0:
        raise ValueError("State must be a root-owned run directory under " + str(STATE_ROOT))
    data = json.loads((state / "state.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"deerflow-port-guard-[0-9]+-[0-9]+", data["unit"]):
        raise ValueError("Invalid rollback unit.")
    return state, data


def table_exists():
    # A failure to inspect the ruleset is an error, never evidence of absence.
    result = command(["nft", "-j", "list", "tables"])
    return any(item.get("table", {}).get("name") == TABLE and
               item.get("table", {}).get("family") == "inet"
               for item in json.loads(result.stdout)["nftables"])


def stop_timer(data):
    command(["systemctl", "stop", data["unit"] + ".timer", data["unit"] + ".service"])


def apply(policy, minutes, console_access=False):
    require_root()
    if not 5 <= minutes <= 60:
        raise ValueError("Rollback delay must be 5..60 minutes.")
    for executable in ("nft", "systemd-run", "systemctl"):
        if not shutil.which(executable):
            raise RuntimeError("Missing " + executable + "; no packages are installed automatically.")
    check_ssh(policy, console_access)
    for interface in policy["interfaces"]:
        if not (Path("/sys/class/net") / interface).exists():
            raise ValueError("Interface not present: " + interface)
    with locked():
        if table_exists():
            raise RuntimeError("Guard table already exists. Verify or roll back the previous run first.")
        STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
        if STATE_ROOT.is_symlink() or STATE_ROOT.stat().st_uid != 0:
            raise RuntimeError("Unsafe state directory.")
        os.chmod(str(STATE_ROOT), 0o700)
        run_id = "{}-{}".format(int(time.time()), os.getpid())
        state = STATE_ROOT / run_id
        state.mkdir(mode=0o700)
        rules = state / "rules.nft"
        rules.write_text(render(policy, run_id), encoding="utf-8")
        (state / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
        shutil.copyfile(str(Path(__file__).resolve()), str(state / "guard.py"))
        (state / "before.nft").write_text(command(["nft", "list", "ruleset"]).stdout, encoding="utf-8")
        command(["nft", "-c", "-f", str(rules)])
        data = {"unit": "deerflow-port-guard-" + run_id, "owner": run_id, "status": "prepared",
                "deadline": int(time.time()) + minutes * 60}
        save(state, data)
        # Arm rollback before changing packet filtering. This survives SSH loss.
        command(["systemd-run", "--unit=" + data["unit"], "--on-active={}min".format(minutes),
                 "--timer-property=AccuracySec=1s",
                 str(Path(sys.executable).resolve()), str(state / "guard.py"),
                 "rollback", "--state", str(state)])
        try:
            command(["systemctl", "is-active", data["unit"] + ".timer"])
            command(["nft", "-f", str(rules)])
            data["status"] = "active"
            save(state, data)
        except Exception:
            # nft loads the batch atomically; remove only our table if present.
            if table_exists():
                check_owner(data)
                command(["nft", "delete", "table", "inet", TABLE])
            data["status"] = "rolled_back"
            save(state, data)
            stop_timer(data)
            raise
        print("Applied runtime rules. State: " + str(state))
        print("Automatic rollback in {} minutes; validate from another machine first.".format(minutes))
        print("Confirm: sudo python3 {} confirm --state {} --verified".format(state / "guard.py", state))
        print("Rollback: sudo python3 {} rollback --state {}".format(state / "guard.py", state))
        print("Rules are NOT persisted across reboot or firewall reload; rules.nft is the reviewed artifact.")


def check_owner(data):
    result = command(["nft", "-j", "list", "table", "inet", TABLE])
    marker = "deerflow-owner-" + data["owner"]
    if not any(item.get("rule", {}).get("comment") == marker
               for item in json.loads(result.stdout)["nftables"]):
        raise RuntimeError("Guard belongs to another run; refusing to alter it.")


def finish(action, path, verified):
    with locked():
        state, data = read_state(path)
        if action == "confirm":
            if not verified or data["status"] != "active":
                raise ValueError("Confirmation requires an active run and --verified after external checks.")
            if time.time() >= data["deadline"] or not table_exists():
                raise RuntimeError("Rollback deadline passed or guard absent; cannot confirm.")
            check_owner(data)
            command(["systemctl", "is-active", data["unit"] + ".timer"])
            stop_timer(data)
            data["status"] = "confirmed"
        else:
            if data["status"] not in ("prepared", "active", "confirmed"):
                print("Already rolled back.")
                return
            if table_exists():
                check_owner(data)
                command(["nft", "delete", "table", "inet", TABLE])
            data["status"] = "rolled_back"
            # Do not stop the running rollback service from inside itself.
            command(["systemctl", "stop", data["unit"] + ".timer"], check=False)
        save(state, data)
        print(data["status"] + ": " + str(state))


def audit():
    probes = {"listeners": ["ss", "-lntup"], "interfaces": ["ip", "-br", "addr"],
              "routes": ["ip", "route"],
              "nfs_rpc": ["rpcinfo", "-p"], "nfs_exports": ["exportfs", "-v"],
              "kubernetes_services": ["kubectl", "get", "svc", "-A", "-o", "wide"],
              "kubernetes_nodes": ["kubectl", "get", "nodes", "-o", "wide"],
              "firewalld": ["firewall-cmd", "--list-all-zones"],
              "guard": ["nft", "list", "table", "inet", TABLE]}
    for name, argv in probes.items():
        print("\n[" + name + "]")
        try:
            result = command(argv, check=False)
            print(result.stdout[:24000] + result.stderr[:4000])
            print("exit_code=" + str(result.returncode))
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("UNKNOWN: " + str(exc))
    print("Manual review required: listeners alone do not prove remote reachability.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="audit", choices=["audit", "plan", "apply", "confirm", "rollback"])
    parser.add_argument("--policy")
    parser.add_argument("--state")
    parser.add_argument("--rollback-minutes", type=int, default=10)
    parser.add_argument("--verified", action="store_true")
    parser.add_argument("--console-access", action="store_true", help="Apply from verified console when SSH_CONNECTION is unavailable.")
    args = parser.parse_args()
    try:
        if args.action == "audit":
            audit()
        elif args.action in ("plan", "apply"):
            if not args.policy:
                raise ValueError("--policy is required.")
            policy = validate(json.loads(Path(args.policy).read_text(encoding="utf-8-sig")))
            if args.action == "plan":
                print(render(policy))
            else:
                apply(policy, args.rollback_minutes, args.console_access)
        else:
            if not args.state:
                raise ValueError("--state is required.")
            finish(args.action, args.state, args.verified)
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
