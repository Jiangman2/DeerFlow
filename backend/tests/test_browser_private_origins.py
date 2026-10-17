import ipaddress

import pytest

from deerflow.community.browser_automation.url_policy import validate_private_origin

POLICY = [{"origin": "https://portal.example.test", "cidrs": ["10.56.10.0/24"]}]


def resolve(*ips):
    return lambda _: [ipaddress.ip_address(ip) for ip in ips]


def test_exact_origin_and_all_resolved_addresses_must_match():
    assert validate_private_origin("https://portal.example.test/path?q=1", POLICY, resolver=resolve("10.56.10.20")) is None
    assert validate_private_origin("https://portal.example.test", POLICY, resolver=resolve("10.56.10.20", "10.57.1.20"))
    for url in ["http://portal.example.test", "https://portal.example.test:8443", "https://portal.example.test.evil.test", "https://user:password@portal.example.test", "file:///etc/passwd"]:
        assert validate_private_origin(url, POLICY, resolver=resolve("10.56.10.20"))


@pytest.mark.parametrize("ip", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "224.0.0.1", "::1", "fd00:ec2::254", "168.63.129.16", "100.100.100.200"])
def test_local_and_metadata_endpoints_never_accepted(ip):
    assert validate_private_origin("https://portal.example.test", POLICY, resolver=resolve(ip))


@pytest.mark.parametrize(
    "policy", [None, "*", [{"origin": "https://*.example.test", "cidrs": ["10.0.0.0/8"]}], [{"origin": "https://portal.example.test/path", "cidrs": ["10.0.0.0/8"]}], [{"origin": "https://portal.example.test", "cidrs": ["0.0.0.0/0"]}]]
)
def test_invalid_configuration_fails_closed(policy):
    assert validate_private_origin("https://portal.example.test", policy, resolver=resolve("10.56.10.20"))


def test_dns_failure_is_not_allowed():
    assert validate_private_origin("https://portal.example.test", POLICY, resolver=lambda _: [])


def test_explicit_private_ip_with_port_is_supported():
    policy = [{"origin": "https://10.56.10.20:8443", "cidrs": ["10.56.10.20/32"]}]
    assert validate_private_origin("https://10.56.10.20:8443/app", policy) is None
