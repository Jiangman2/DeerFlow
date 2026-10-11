"""Synthetic publication races plus real temporary SQLite writer serialization."""

import ast
import asyncio
import copy
import json
import shutil
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.digital_employee_square import DigitalEmployeeSquareRepository, SquareSettingsBusy, SquareSettingsConflict

ROUTER_SOURCE = Path(__file__).resolve().parents[1] / "app/gateway/routers/agents.py"


def route_function(name, globals_):
    """Compile the actual route/helper body, with no live application imports."""
    node = copy.deepcopy(next(n for n in ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8")).body if isinstance(n, ast.AsyncFunctionDef) and n.name == name))
    node.decorator_list = []
    node.returns = None
    for arg in node.args.args:
        arg.annotation = None
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROUTER_SOURCE), "exec"), globals_)
    return globals_[name]


class HttpError(Exception):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        super().__init__(detail)


@pytest.fixture
def publication(tmp_path):
    personal, team, legacy = (tmp_path / name for name in ("personal", "team", "legacy"))
    for directory in (personal, team, legacy):
        directory.mkdir()
    (team / "config.yaml").write_text(json.dumps({"published_by_user_id": "owner", "description": "reviewed"}), encoding="utf-8")
    (team / "SOUL.md").write_text("reviewed soul", encoding="utf-8")
    state = {"hidden": True, "version": 1}
    evidence = {"guard": False, "file_writes": 0, "restore_before_entry": False, "busy": False}

    class Edit:
        async def get(self):
            assert evidence["guard"]
            return copy.deepcopy(state)

        async def patch(self, version, **changes):
            assert evidence["guard"]
            if version != state["version"]:
                raise SquareSettingsConflict()
            state.update(hidden=changes["hidden"], version=version + 1)

    @asynccontextmanager
    async def guard(key):
        assert key == "team:example"
        if evidence["busy"]:
            raise SquareSettingsBusy()
        if evidence["restore_before_entry"]:
            state.update(hidden=False, version=2)
        evidence["guard"] = True
        try:
            yield Edit()
        finally:
            evidence["guard"] = False

    async def file_io(function):
        assert evidence["guard"]
        result = function()
        evidence["file_writes"] += 1
        return result

    async def current_user(request):
        return SimpleNamespace(id="owner", system_role="user")

    globals_ = {
        "AgentResponse": SimpleNamespace, "_publication_square_edit": guard,
        "_publication_file_io": file_io, "get_current_user_from_request": current_user,
        "_require_agents_api_enabled": lambda: None, "_validate_agent_name": lambda name: None,
        "_normalize_agent_name": lambda name: name, "get_effective_user_id": lambda: "owner",
        "get_paths": lambda: SimpleNamespace(user_agent_dir=lambda *a: personal, team_agent_dir=lambda *a: team, agent_dir=lambda *a: legacy),
        "is_personal_agent": lambda *a, **k: True,
        "load_agent_config": lambda *a, **k: SimpleNamespace(model_dump=lambda **kw: {"description": "new private snapshot"}),
        "resolve_digital_employee_category": lambda value: "test", "load_agent_soul": lambda *a, **k: "new private soul",
        "_avatar_path": lambda directory: directory / "avatar.webp", "_shared_agent_response": lambda *a, **k: SimpleNamespace(),
        "yaml": SimpleNamespace(safe_load=json.load, safe_dump=lambda value, file, **kw: json.dump(value, file)),
        "shutil": shutil, "HTTPException": HttpError, "SquareSettingsConflict": SquareSettingsConflict,
        "SquareSettingsBusy": SquareSettingsBusy, "logger": SimpleNamespace(error=lambda *a, **k: None),
    }
    invoke = route_function("publish_agent", globals_)
    return SimpleNamespace(invoke=invoke, state=state, evidence=evidence, team=team, request=SimpleNamespace(state=SimpleNamespace(auth_source="session")))


@pytest.mark.asyncio
async def test_admin_restored_before_guard_rejects_before_snapshot_changes(publication):
    publication.evidence["restore_before_entry"] = True
    before = {name: (publication.team / name).read_bytes() for name in ("config.yaml", "SOUL.md")}
    with pytest.raises(HttpError) as error:
        await publication.invoke("example", SimpleNamespace(overwrite=True), publication.request)
    assert error.value.status_code == 403
    assert publication.evidence["file_writes"] == 0
    assert {name: (publication.team / name).read_bytes() for name in before} == before
    assert publication.state == {"hidden": False, "version": 2}


@pytest.mark.asyncio
async def test_withdrawn_owner_copy_and_visibility_stay_inside_guard(publication):
    await publication.invoke("example", SimpleNamespace(overwrite=True), publication.request)
    assert publication.state == {"hidden": False, "version": 2}
    assert not publication.evidence["guard"]
    assert json.loads((publication.team / "config.yaml").read_text())["description"] == "new private snapshot"
    assert (publication.team / "SOUL.md").read_text() == "new private soul"


@pytest.mark.asyncio
async def test_busy_guard_rejects_before_snapshot_changes(publication):
    publication.evidence["busy"] = True
    before = (publication.team / "config.yaml").read_bytes()
    with pytest.raises(HttpError) as error:
        await publication.invoke("example", SimpleNamespace(overwrite=True), publication.request)
    assert error.value.status_code == 503
    assert (publication.team / "config.yaml").read_bytes() == before


@pytest.mark.asyncio
async def test_no_overwrite_does_not_change_withdrawal_or_files(publication):
    with pytest.raises(HttpError) as error:
        await publication.invoke("example", SimpleNamespace(overwrite=False), publication.request)
    assert error.value.status_code == 409
    assert publication.state == {"hidden": True, "version": 1}
    assert (publication.team / "SOUL.md").read_text() == "reviewed soul"


@pytest.mark.asyncio
async def test_cancelled_file_worker_finishes_before_guard_can_release():
    helper = route_function("_publication_file_io", {"asyncio": asyncio})
    started, finish, exited = threading.Event(), threading.Event(), threading.Event()

    def worker():
        started.set()
        assert finish.wait(5)
        exited.set()

    task = asyncio.create_task(helper(worker))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()  # Repeated cancellation must not abandon the IO thread.
        await asyncio.sleep(0.02)
        assert not task.done()
        assert not exited.is_set()
    finally:
        finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert exited.is_set()


@pytest.mark.asyncio
async def test_worker_cancelled_error_does_not_loop_forever():
    helper = route_function("_publication_file_io", {"asyncio": asyncio})

    def worker():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(helper(worker), timeout=1)


@pytest.mark.asyncio
async def test_withdrawal_rechecks_publisher_after_guard_entry():
    evidence = {"guard": False, "checks": 0, "writes": 0}

    async def authorize(key, request):
        evidence["checks"] += 1
        if evidence["guard"]:
            raise HttpError(403, "Publisher changed during lock wait")

    class Repo:
        @asynccontextmanager
        async def edit(self, key):
            evidence["guard"] = True
            try:
                yield self
            finally:
                evidence["guard"] = False

        async def patch(self, **kwargs):
            evidence["writes"] += 1

    async def actor(request):
        return SimpleNamespace(id="old-owner")

    body = SimpleNamespace(key="team:example", version=1, hidden=True, model_fields_set={"key", "version", "hidden"}, model_dump=lambda **kw: {"hidden": True})
    function = route_function("update_square_settings", {
        "_require_agents_api_enabled": lambda: None, "_require_square_withdrawal": authorize,
        "get_current_user_from_request": actor, "_square_repo": Repo, "HTTPException": HttpError,
        "SquareSettingsConflict": SquareSettingsConflict, "SquareSettingsBusy": SquareSettingsBusy,
    })
    with pytest.raises(HttpError) as error:
        await function(body, SimpleNamespace())
    assert error.value.status_code == 403
    assert evidence == {"guard": False, "checks": 2, "writes": 0}


@pytest_asyncio.fixture
async def sqlite_repos(tmp_path):
    url = "sqlite+aiosqlite:///" + (tmp_path / "square.sqlite").as_posix()
    first = create_async_engine(url, connect_args={"timeout": 1})
    second = create_async_engine(url, connect_args={"timeout": 1})
    async with first.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield DigitalEmployeeSquareRepository(async_sessionmaker(first, expire_on_commit=False)), DigitalEmployeeSquareRepository(async_sessionmaker(second, expire_on_commit=False))
    await first.dispose()
    await second.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_independent_sqlite_sessions_serialize_even_absent_rows(sqlite_repos, existing):
    first, second = sqlite_repos
    key = "team:example"
    if existing:
        await first.patch(key, version=0, metadata={}, updated_by="owner", hidden=True)
    entered = asyncio.Event()

    async def contender():
        async with second.edit(key) as edit:
            entered.set()
            current = await edit.get()
            return await edit.patch(version=current["version"], metadata={}, updated_by="admin", hidden=True)

    async with first.edit(key) as edit:
        current = await edit.get()
        await edit.patch(version=current["version"] if current else 0, metadata={}, updated_by="owner", hidden=False)
        task = asyncio.create_task(contender())
        await asyncio.sleep(0.05)
        assert not entered.is_set()
    result = await asyncio.wait_for(task, timeout=2)
    assert result["hidden"] is True
    assert result["version"] == (3 if existing else 2)


@pytest.mark.asyncio
async def test_stale_cas_and_failed_edit_leave_database_unchanged(sqlite_repos):
    first, second = sqlite_repos
    await first.patch("team:example", version=0, metadata={"display_name": "reviewed"}, updated_by="admin", hidden=True)
    with pytest.raises(SquareSettingsConflict):
        await second.patch("team:example", version=0, metadata={}, updated_by="owner", hidden=False)
    with pytest.raises(RuntimeError):
        async with second.edit("team:example") as edit:
            await edit.patch(version=1, metadata={}, updated_by="owner", hidden=False)
            raise RuntimeError("synthetic failure")
    current = await first.get("team:example")
    assert current["version"] == 1
    assert current["hidden"] is True


@pytest.mark.asyncio
async def test_sqlite_busy_is_bounded_and_does_not_write(sqlite_repos):
    first, second = sqlite_repos
    async with first.edit("team:example"):
        with pytest.raises(SquareSettingsBusy):
            await asyncio.wait_for(second.patch("team:example", version=0, metadata={}, updated_by="other", hidden=False), timeout=2)
    assert await first.get("team:example") is None


@pytest.mark.asyncio
async def test_postgres_guard_uses_stable_transaction_key_before_yield():
    events = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            events.append("session-exit")

        @asynccontextmanager
        async def begin(self):
            events.append("begin")
            yield
            events.append("commit")

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement, params):
            assert str(statement) == "SELECT pg_try_advisory_xact_lock(:key)"
            assert -(2**63) <= params["key"] < 2**63
            events.append(params["key"])
            return SimpleNamespace(scalar_one=lambda: True)

    repo = DigitalEmployeeSquareRepository(Session)
    for _ in range(2):
        async with repo.edit("team:example"):
            assert events[-2] == "begin"
            events.append("copy")
    assert events[1] == events[6]
    assert events[2:5] == ["copy", "commit", "session-exit"]
