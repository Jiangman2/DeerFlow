"""Create Redis clients for a direct URL or an explicit Sentinel primary.

The custom ``sentinel://`` form is an enterprise deployment adapter. It keeps
the existing stream-bridge and sandbox-ownership configuration shape while
allowing both clients to follow the same elected primary after failover.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass(frozen=True)
class SentinelEndpoint:
    servers: tuple[tuple[str, int], ...]
    master_name: str
    db: int
    password: str
    sentinel_password: str | None


def parse_sentinel_endpoint(redis_url: str) -> SentinelEndpoint | None:
    """Parse a dedicated three-Sentinel URL without exposing its secret.

    Format: ``sentinel://h1:26379,h2:26379,h3:26379/master/6?password=...``.
    Password values must be URL-encoded. Any malformed/partial Sentinel URL
    fails closed instead of silently falling back to a single Redis address.
    """
    if not redis_url.startswith("sentinel:"):
        return None
    try:
        parsed = urlsplit(redis_url)
        if parsed.scheme != "sentinel" or parsed.fragment or "@" in parsed.netloc:
            raise ValueError
        raw_servers = parsed.netloc.split(",")
        if len(raw_servers) != 3:
            raise ValueError
        servers: list[tuple[str, int]] = []
        for raw_server in raw_servers:
            server = urlsplit(f"redis://{raw_server}")
            host, port = server.hostname, server.port
            if not host or port is None or not 1 <= port <= 65535 or server.username or server.password or server.path:
                raise ValueError
            servers.append((host, port))
        if len(set(servers)) != 3:
            raise ValueError
        path = parsed.path.strip("/").split("/")
        if len(path) != 2 or not path[0] or not path[1].isdigit():
            raise ValueError
        master_name, db = unquote(path[0]), int(path[1])
        if not master_name or not 0 <= db <= 15:
            raise ValueError
        query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
        if not query or set(query) - {"password", "sentinel_password"}:
            raise ValueError
        if len(query.get("password", [])) != 1 or not query["password"][0]:
            raise ValueError
        sentinel_password = query.get("sentinel_password")
        if sentinel_password is not None and (len(sentinel_password) != 1 or not sentinel_password[0]):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        # Even a parser exception can contain user-supplied URL text. Do not
        # chain it into a startup traceback that could expose a credential.
        raise ValueError("Invalid Sentinel Redis endpoint configuration") from None
    return SentinelEndpoint(
        servers=tuple(servers),
        master_name=master_name,
        db=db,
        password=query["password"][0],
        sentinel_password=sentinel_password[0] if sentinel_password else None,
    )


def make_sync_redis_client(redis_url: str, *, socket_timeout: float):
    """Return a lazy synchronous client; no network call during construction."""
    from redis import Redis

    endpoint = parse_sentinel_endpoint(redis_url)
    if endpoint is None:
        return Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
        )

    from redis.sentinel import Sentinel

    sentinel_kwargs = {"socket_timeout": socket_timeout, "socket_connect_timeout": socket_timeout}
    if endpoint.sentinel_password is not None:
        sentinel_kwargs["password"] = endpoint.sentinel_password
    sentinel = Sentinel(endpoint.servers, sentinel_kwargs=sentinel_kwargs)
    return sentinel.master_for(
        endpoint.master_name,
        db=endpoint.db,
        password=endpoint.password,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=socket_timeout,
    )


def make_async_redis_client(redis_url: str, *, max_connections: int | None):
    """Return a lazy asynchronous client for Redis Streams subscribers."""
    from redis.asyncio import Redis

    endpoint = parse_sentinel_endpoint(redis_url)
    if endpoint is None:
        return Redis.from_url(redis_url, decode_responses=True, max_connections=max_connections)

    from redis.asyncio.sentinel import Sentinel

    sentinel_kwargs = {"socket_timeout": 5.0, "socket_connect_timeout": 5.0}
    if endpoint.sentinel_password is not None:
        sentinel_kwargs["password"] = endpoint.sentinel_password
    sentinel = Sentinel(endpoint.servers, sentinel_kwargs=sentinel_kwargs)
    return sentinel.master_for(
        endpoint.master_name,
        db=endpoint.db,
        password=endpoint.password,
        decode_responses=True,
        max_connections=max_connections,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
    )
