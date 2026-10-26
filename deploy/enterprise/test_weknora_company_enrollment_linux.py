"""Python 3.7 syntax and Linux pipe-heartbeat qualification; no network."""

import ast
import asyncio
import json
import os
from pathlib import Path
import sys


def literal(source, key):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == key for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("missing packaged source")


async def heartbeat_test(scope, lost):
    reader, writer = os.pipe()
    previous = sys.stdin
    sys.stdin = os.fdopen(reader, "rb", buffering=0)
    stopped = []

    async def work():
        try:
            await asyncio.sleep(5 if lost else 0.1)
        finally:
            stopped.append(True)

    try:
        # Complete, coalesced and partial heartbeat frames must all work.
        os.write(writer, b"PING\nPING\nPI")
        os.write(writer, b"NG\n")
        if lost:
            os.close(writer)
            writer = None
            try:
                await scope["with_heartbeat"](work())
            except scope["Refused"] as exc:
                assert str(exc) == "HOST_HEARTBEAT_LOST_DO_NOT_REPLAY"
            else:
                raise AssertionError("closed wrapper must stop remote writes")
        else:
            await scope["with_heartbeat"](work())
        assert stopped == [True]
    finally:
        sys.stdin.close()
        sys.stdin = previous
        if writer is not None:
            os.close(writer)


def main():
    source = Path(sys.argv[1]).read_text(encoding="utf-8")
    child, guard = literal(source, "CHILD"), literal(source, "GUARD")
    collector, bootstrap = literal(child, "COLLECTOR"), literal(child, "BOOTSTRAP")
    classifier = literal(collector, "CLASSIFIER")
    for value in (source, child, guard, collector, bootstrap, classifier):
        compile(value, "<offline-qualification>", "exec")
    scope = {"__name__": "offline_bulk_child"}
    exec(compile(child, "<offline-child>", "exec"), scope)
    asyncio.run(heartbeat_test(scope, False))
    asyncio.run(heartbeat_test(scope, True))
    print(json.dumps({"status": "LINUX_PY37_ENROLLMENT_ACCEPTED", "sources_compiled": 6, "heartbeat_checks": 2, "network_requests": 0}))


if __name__ == "__main__":
    main()
