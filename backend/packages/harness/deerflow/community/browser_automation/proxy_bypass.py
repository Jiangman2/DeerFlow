"""Operator-owned Chromium direct routing, independent of URL authorization."""

import os
import re


def browser_proxy_bypass_args() -> list[str]:
    """Append direct routes without changing proxies, TLS, or private URL policy.

    Chromium does not use the HTTP client's NO_PROXY contract. Keep this opt-in
    setting explicit and separate; do not turn arbitrary NO_PROXY tokens (CIDRs,
    wildcard-all, credentials, ports) into Chromium command-line switches.
    """
    value = os.environ.get("DEERFLOW_BROWSER_DIRECT_SUFFIXES", "")
    if not value:
        return []
    if len(value) > 4096:
        raise ValueError("Invalid browser direct suffix configuration")
    suffixes = value.split(",")
    if len(suffixes) > 32:
        raise ValueError("Invalid browser direct suffix configuration")
    rules = []
    for item in suffixes:
        host = item.strip().lower()
        labels = host.split(".")
        if not host or len(host) > 253 or (len(labels) < 2 and host != "cnooc") or all(label.isdigit() for label in labels) or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            # Never include the supplied value in logs or errors.
            raise ValueError("Invalid browser direct suffix configuration")
        for rule in (host, "*." + host):
            if rule not in rules:
                rules.append(rule)
    return ["--proxy-bypass-list=" + ";".join(rules)]
