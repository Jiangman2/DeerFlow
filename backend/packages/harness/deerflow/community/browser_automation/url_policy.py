"""Narrow, operator-owned exceptions to the public-only browser URL policy.

Application checks complement, not replace, browser egress restrictions. DNS is
resolved again by Chromium; restrict network egress to prevent DNS rebinding.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from deerflow.community.url_safety import resolve_host_addresses, validate_public_http_url

_PRIVATE_RANGES = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
_METADATA = {ipaddress.ip_address(value) for value in ("169.254.169.254", "100.100.100.200", "168.63.129.16", "fd00:ec2::254")}


def _origin(url):
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url) or "\\" in url:
        raise ValueError("Invalid URL")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("Invalid URL")
    hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    if any(char in hostname for char in ("*", "%")):
        raise ValueError("Invalid host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (parsed.scheme, hostname, port), parsed


def _parse_policy(entries):
    if not isinstance(entries, list) or not entries or len(entries) > 64:
        raise ValueError("Invalid private origins")
    policy = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"origin", "cidrs"}:
            raise ValueError("Invalid private origin")
        origin, parsed = _origin(entry["origin"])
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment or origin in policy:
            raise ValueError("Origins must be exact and unique")
        cidrs = entry["cidrs"]
        if not isinstance(cidrs, list) or not 1 <= len(cidrs) <= 32:
            raise ValueError("Missing address ranges")
        networks = [ipaddress.ip_network(value, strict=True) for value in cidrs]
        if any(not any(network.version == allowed.version and network.subnet_of(allowed) for allowed in _PRIVATE_RANGES) for network in networks):
            raise ValueError("Only explicit private networks are allowed")
        policy[origin] = networks
    return policy


def _parse_suffix_policy(entries):
    """Explicit operator-owned base domains, distinct from exact-origin entries."""
    if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
        raise ValueError("Invalid private origin suffixes")
    exact_entries = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"origin_suffix", "cidrs"}:
            raise ValueError("Invalid private origin suffix")
        origin, _ = _origin(entry["origin_suffix"])
        host = origin[1]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("Suffix must be a DNS domain")
        labels = host.split(".")
        # The enterprise also owns the internal single-label DNS namespace
        # .cnooc (for example datalake.cnooc). Do not generalize this exception
        # to public TLDs such as .com; an explicit operator rule and CIDRs are
        # still required and the public-only default remains unchanged.
        if (len(labels) < 2 and host != "cnooc") or len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise ValueError("Invalid base domain")
        exact_entries.append({"origin": entry["origin_suffix"], "cidrs": entry["cidrs"]})
    return _parse_policy(exact_entries)


def _check_addresses(origin, networks, resolver):
    hostname = origin[1]
    if hostname == "localhost" or hostname.endswith(".localhost") or hostname == "metadata.google.internal":
        return "Error: Loopback and metadata targets are not permitted"
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        addresses = resolver(hostname)
    if not addresses:
        return "Error: Enterprise host could not be resolved"
    for address in addresses:
        if address in _METADATA or address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            return "Error: Loopback and metadata targets are not permitted"
        if not any(address.version == network.version and address in network for network in networks):
            return "Error: Enterprise host resolved outside its approved address ranges"
    return None


def validate_private_origin(url, entries, *, resolver=resolve_host_addresses):
    try:
        origin, _ = _origin(url)
        policy = _parse_policy(entries)
        if origin not in policy:
            return "Error: Enterprise URL origin is not approved"
        return _check_addresses(origin, policy[origin], resolver)
    except (ValueError, TypeError, UnicodeError):
        return "Error: Invalid enterprise browser URL or private_origins configuration"


def validate_browser_url_policy(url, entries=None, *, private_origin_suffixes=None, resolver=resolve_host_addresses):
    try:
        origin, _ = _origin(url)
        if entries is None and private_origin_suffixes is None:
            raise ValueError("Missing browser policy")
        policy = _parse_policy(entries) if entries is not None else {}
        suffixes = _parse_suffix_policy(private_origin_suffixes) if private_origin_suffixes is not None else {}
        # An explicit origin overrides a broader domain rule, including denials.
        if origin in policy:
            return _check_addresses(origin, policy[origin], resolver)
        matching = [base for base in suffixes if origin[0] == base[0] and origin[2] == base[2] and (origin[1] == base[1] or origin[1].endswith("." + base[1]))]
        if matching:
            # Most-specific configured domain wins; never union its CIDRs with a parent.
            base = max(matching, key=lambda item: len(item[1]))
            return _check_addresses(origin, suffixes[base], resolver)
    except (ValueError, TypeError, UnicodeError):
        return "Error: Invalid enterprise browser URL or private origins configuration"
    return validate_public_http_url(url, action="browse", resolver=resolver)
