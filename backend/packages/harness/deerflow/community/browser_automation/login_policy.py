"""Manual login privacy configuration (does not grant network access)."""

from .url_policy import _origin


def manual_login_origins(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Invalid manual_login_origins")
    result = []
    for origin in value:
        if not isinstance(origin, str) or len(origin) > 512:
            raise ValueError("Invalid manual login origin")
        (scheme, hostname, port), parsed = _origin(origin)
        if scheme != "https" or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Manual login origins must be exact HTTPS origins")
        host = "[" + hostname + "]" if ":" in hostname else hostname
        normalized = "https://" + host
        if port and port != 443:
            normalized += ":" + str(port)
        result.append(normalized)
    return tuple(result)
