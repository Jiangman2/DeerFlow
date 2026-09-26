"""Regression tests for the generated OpenAPI spec.

The Gateway exposes its FastAPI ``app.openapi()`` schema at ``/openapi.json``
and downstream tooling (SDK codegen, schema validators, client generators)
relies on ``operationId`` values being globally unique. FastAPI emits a
``UserWarning`` during spec generation when two routes share the same
``operationId`` — concretely this happens when ``@router.api_route`` registers
one route for multiple HTTP methods, because the auto-generated unique id is
computed from a single method picked out of ``route.methods`` while OpenAPI
generation iterates over every method on that route.

These tests pin that invariant so the warning cannot silently come back.
"""

from __future__ import annotations

import warnings

import pytest


@pytest.fixture(scope="module")
def openapi_spec() -> dict:
    """Build the OpenAPI spec for the Gateway app once per module."""
    from app.gateway.app import app

    # ``app.openapi()`` caches the result on the FastAPI instance, so reset to
    # force a fresh generation pass that triggers any duplicate-id warnings.
    app.openapi_schema = None
    return app.openapi()


def test_openapi_spec_has_no_duplicate_operation_warnings() -> None:
    """Generating the OpenAPI schema must not emit any ``Duplicate Operation ID`` UserWarning."""
    from app.gateway.app import app

    app.openapi_schema = None
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        app.openapi()

    dup_messages = [str(item.message) for item in caught if "Duplicate Operation ID" in str(item.message)]
    assert dup_messages == [], f"OpenAPI generation emitted duplicate operation id warnings: {dup_messages}"


def test_openapi_operation_ids_are_unique(openapi_spec: dict) -> None:
    """Every (path, method) operation in the spec must carry a unique ``operationId``."""
    op_id_to_locations: dict[str, list[tuple[str, str]]] = {}

    for path, path_item in openapi_spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if op_id is None:
                continue
            op_id_to_locations.setdefault(op_id, []).append((path, method))

    duplicates = {op_id: locations for op_id, locations in op_id_to_locations.items() if len(locations) > 1}
    assert not duplicates, f"Duplicate operationIds in OpenAPI spec: {duplicates}"


def test_stream_existing_run_exposes_distinct_get_and_post(openapi_spec: dict) -> None:
    """The ``/runs/{run_id}/stream`` endpoint must expose GET and POST as distinct operations.

    LangGraph SDK ``joinStream`` uses GET while ``useStream``'s stop button uses POST, so
    both methods must remain registered with their own ``operationId``.
    """
    path = "/api/threads/{thread_id}/runs/{run_id}/stream"
    path_item = openapi_spec["paths"].get(path)
    assert path_item is not None, f"Expected {path} to be present in the OpenAPI spec"

    assert "get" in path_item, f"Expected GET handler on {path}"
    assert "post" in path_item, f"Expected POST handler on {path}"

    get_op_id = path_item["get"].get("operationId")
    post_op_id = path_item["post"].get("operationId")
    assert get_op_id and post_op_id, "Both GET and POST must have operationIds"
    assert get_op_id != post_op_id, f"GET and POST share operationId {get_op_id!r}, which breaks OpenAPI codegen"


@pytest.mark.parametrize(
    ("path", "methods"),
    [
        ("/api/threads/{thread_id}/live-preview/{port}/{path}", {"get", "head", "options", "post", "put", "patch", "delete"}),
        ("/api/threads/{thread_id}/artifacts/{path}", {"get", "head"}),
    ],
)
def test_enterprise_artifact_methods_remain_independent_operations(openapi_spec, path, methods):
    operations = openapi_spec["paths"][path]
    assert methods <= operations.keys()
    assert len({operations[method]["operationId"] for method in methods}) == len(methods)


def test_enterprise_method_routes_share_the_original_authorization_wrapper():
    from app.gateway.routers.artifacts import get_artifact, get_live_preview, mutate_live_preview, router

    for endpoint, methods in (
        (get_artifact, {"GET", "HEAD"}),
        (get_live_preview, {"GET", "HEAD", "OPTIONS"}),
        (mutate_live_preview, {"POST", "PUT", "PATCH", "DELETE"}),
    ):
        # Every method registers the same permission-decorated callable, never
        # its unwrapped implementation. Splitting schema IDs cannot drop RBAC.
        assert hasattr(endpoint, "__wrapped__")
        routes = [route for route in router.routes if route.endpoint is endpoint]
        assert {method for route in routes for method in route.methods} == methods
        assert all(len(route.methods) == 1 for route in routes)
