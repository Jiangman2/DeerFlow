"""Narrow, operator-owned exceptions to the public-only browser URL policy.

Application checks complement, not replace, browser egress restrictions. DNS is
resolved again by Chromium; restrict network egress to prevent DNS rebinding.
"""

import ipaddress
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


def validate_browser_url_policy(url, entries, *, resolver=resolve_host_addresses):
    try:
        origin, _ = _origin(url)
        policy = _parse_policy(entries)
        if origin in policy:
            return _check_addresses(origin, policy[origin], resolver)
    except (ValueError, TypeError, UnicodeError):
        return "Error: Invalid enterprise browser URL or private_origins configuration"
    return validate_public_http_url(url, action="browse", resolver=resolver)
