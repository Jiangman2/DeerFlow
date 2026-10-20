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


SUFFIX_POLICY = [{"origin_suffix": "https://enterprise.example.test", "cidrs": ["10.56.10.0/24"]}]


def suffix_check(url, *, policy=SUFFIX_POLICY, ips=("10.56.10.20",), exact=None):
    from deerflow.community.browser_automation.url_policy import validate_browser_url_policy

    return validate_browser_url_policy(url, exact, private_origin_suffixes=policy, resolver=resolve(*ips))


@pytest.mark.parametrize("host", ["enterprise.example.test", "portal.enterprise.example.test", "login.sso.enterprise.example.test", "PORTAL.ENTERPRISE.EXAMPLE.TEST."])
def test_suffix_matches_base_and_dot_boundary_subdomains(host):
    assert suffix_check("https://" + host + "/login") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://enterprise.example.test.evil.test/",
        "https://evilenterprise.example.test/",
        "https://unrelated.example.test/",
        "https://user:password@portal.enterprise.example.test/",
        "https://portal.enterprise.example.test:8443/",
        "http://portal.enterprise.example.test/",
        "https://10.56.10.20/",
        "https://portal.enterprise.example.test\\@evil.test/",
    ],
)
def test_suffix_does_not_open_other_private_origins(url):
    assert suffix_check(url)


@pytest.mark.parametrize("ips", [("10.56.10.20", "10.57.1.20"), ("127.0.0.1",), ("169.254.169.254",), ("100.100.100.200",), ("168.63.129.16",), ("fd00:ec2::254",), ()])
def test_suffix_checks_every_address_and_forbids_sensitive_destinations(ips):
    assert suffix_check("https://portal.enterprise.example.test", ips=ips)


@pytest.mark.parametrize(
    "policy",
    [
        [],
        "*",
        [{"origin_suffix": "https://*.example.test", "cidrs": ["10.0.0.0/8"]}],
        [{"origin_suffix": "https://10.1.2.3", "cidrs": ["10.0.0.0/8"]}],
        [{"origin_suffix": "https://com", "cidrs": ["10.0.0.0/8"]}],
        [{"origin_suffix": "https://enterprise.example.test/path", "cidrs": ["10.0.0.0/8"]}],
        [{"origin_suffix": "https://enterprise.example.test", "cidrs": ["0.0.0.0/0"]}],
        [{"origin_suffix": "https://enterprise.example.test", "cidrs": []}],
        [{"origin_suffix": "https://enterprise.example.test", "cidrs": ["10.0.0.0/8"], "extra": True}],
    ],
)
def test_malformed_suffix_configuration_fails_closed(policy):
    assert suffix_check("https://portal.enterprise.example.test", policy=policy)


def test_exact_origin_and_longer_suffix_restrict_parent_rule():
    exact = [{"origin": "https://portal.enterprise.example.test", "cidrs": ["10.57.1.0/24"]}]
    assert suffix_check("https://portal.enterprise.example.test", exact=exact)
    specific = SUFFIX_POLICY + [{"origin_suffix": "https://team.enterprise.example.test", "cidrs": ["10.57.1.0/24"]}]
    assert suffix_check("https://portal.team.enterprise.example.test", policy=specific)
    assert suffix_check("https://portal.team.enterprise.example.test", policy=specific, ips=("10.57.1.20",)) is None


def test_suffix_does_not_disable_unrelated_public_web_access():
    assert suffix_check("https://public.example.test", ips=("93.184.216.34",)) is None


def test_tool_uses_suffix_rule_without_legacy_private_bypass(monkeypatch):
    from deerflow.community.browser_automation import tools

    monkeypatch.setattr(tools, "_get_tool_config", lambda _: {"private_origin_suffixes": SUFFIX_POLICY})
    monkeypatch.setattr(tools, "_resolve_host_addresses", resolve("10.56.10.20"))
    assert tools.validate_browser_url("https://portal.enterprise.example.test") is None
    assert tools.validate_browser_url("https://unrelated.example.test")
