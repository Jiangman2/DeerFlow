"""Bounded anonymous SkillHub regression probe; never sends real credentials.

Run only when requested. GETs only, no redirects, no cookies, no retries, no
response bodies or Set-Cookie values in output. This tests the public entry,
not native token scopes, authenticated usability, or internal service exposure.
"""
import argparse
import datetime
import json
import urllib.error
import urllib.request

ORIGIN = "https://cnoocclaw.cnooc.com.cn"
MAX_BYTES = 65536
PATHS = (
    "/workspace/skills",
    "/platform/skillhub/api/web/labels",
    "/platform/skillhub/api/web/skills?page=0&size=1",
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def summarize(status, raw, is_page=False):
    result = {"http_status": status, "body_exported": False,
              "body_bytes_read": len(raw), "body_bound_exceeded": len(raw) > MAX_BYTES}
    if is_page:
        result["result"] = "REDIRECT_OBSERVED" if status in (301, 302, 303, 307, 308) else "REVIEW_REQUIRED"
        # A page redirect alone never proves API authorization.
        return result
    result["result"] = "AUTH_REJECTED" if status in (401, 403) else "REVIEW_REQUIRED"
    if len(raw) > MAX_BYTES:
        result["result"] = "REVIEW_REQUIRED"
        return result
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    code = payload.get("code")
    if type(code) is int:
        result["business_code"] = code
    data = payload.get("data")
    if isinstance(data, list):
        result["data_item_count"] = len(data)
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        result["data_item_count"] = len(data["items"])
    if 200 <= status < 300 and type(code) is int and code == 0:
        result["result"] = "UNAUTHENTICATED_BUSINESS_RESPONSE"
    return result


def inspect_one(path, invalid_bearer=False):
    headers = {"Accept": "text/html" if path == PATHS[0] else "application/json",
               "User-Agent": "DeerFlow-Authorized-Anonymous-Auth-Check/3"}
    if invalid_bearer:
        headers["Authorization"] = "Bearer invalid-security-regression-token"
    out = {"path": path, "cookies_sent": False, "valid_credentials_sent": False,
           "invalid_bearer_sent": invalid_bearer}
    try:
        request = urllib.request.Request(ORIGIN + path, headers=headers, method="GET")
        opener = urllib.request.build_opener(NoRedirect())
        try:
            response = opener.open(request, timeout=12)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            out.update(summarize(response.code, response.read(MAX_BYTES + 1), path == PATHS[0]))
    except Exception as exc:
        out.update(result="READ_FAILED", error_class=type(exc).__name__)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect-anonymous", action="store_true", required=True)
    parser.parse_args()
    checks = [inspect_one(PATHS[0])]
    for path in PATHS[1:]:
        checks.extend([inspect_one(path), inspect_one(path, invalid_bearer=True)])
    api_ok = all(row["result"] == "AUTH_REJECTED" for row in checks[1:])
    output = {"schema": "portal-skillhub-anonymous-probe.v1",
              "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "status": "ANONYMOUS_API_REJECTED" if api_ok else "AUTH_REMEDIATION_REQUIRED",
              "origin": ORIGIN, "checks": checks, "automatic_retry": False,
              "configuration_modified": False, "database_access": False,
              "provider_requests": 0, "authenticated_acceptance_pending": True,
              "internal_service_acceptance_pending": True}
    print(json.dumps(output, sort_keys=True))
    return 0 if api_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
