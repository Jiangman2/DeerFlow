"""Dedicated-service task routes; no live provider or credentials required."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from docker.mineru_adapter import app

BASE = "https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02"
QUERY = BASE + "/dynamic/member1/mineru-for-zy-agent/tasks"


class Response:
    def __init__(self, status, data):
        self.status_code = status
        self.data = data
        self.text = "synthetic upstream response"

    def json(self):
        return self.data


class Session:
    def __init__(self, statuses=None, results=None, submission=None):
        self.calls = []
        self.submission = (
            submission
            if submission is not None
            else Response(
                202,
                {
                    "task_id": "task-1",
                    "status_url": "https://untrusted.invalid/private",
                    "result_url": "https://untrusted.invalid/private/result",
                },
            )
        )
        self.statuses = list(statuses or [Response(200, {"status": "completed"})])
        self.results = list(
            results
            or [
                Response(
                    200,
                    {
                        "results": {
                            "document": {
                                "md_content": "Parsed test",
                                "images": {},
                            }
                        }
                    },
                )
            ]
        )

    @staticmethod
    def reply(value):
        if isinstance(value, Exception):
            raise value
        return value

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.reply(self.submission)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.reply((self.results if url.endswith("/result") else self.statuses).pop(0))


@pytest.fixture(autouse=True)
def isolate_query_config(monkeypatch):
    monkeypatch.delenv("MINERU_TASK_QUERY_URL", raising=False)
    monkeypatch.setattr(app.time, "sleep", lambda seconds: None)


def client(session, **kwargs):
    return app.EnterpriseMinerUClient(api_url=BASE, api_token="test-token-not-a-secret", request_mode="async", timeout_seconds=30, poll_interval_seconds=0.001, transient_poll_retries=2, session=session, **kwargs)


def test_new_query_base_from_environment_keeps_submission_once(monkeypatch):
    monkeypatch.setenv("MINERU_TASK_QUERY_URL", QUERY + "/")
    session = Session()
    result = client(session).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert result["results"]["document"]["md_content"] == "Parsed test"
    assert [(m, u) for m, u, _ in session.calls] == [
        ("POST", BASE + "/tasks"),
        ("GET", QUERY + "/task-1"),
        ("GET", QUERY + "/task-1/result"),
    ]
    assert all(c[2]["allow_redirects"] is False for c in session.calls)
    assert all(c[2]["headers"]["Authorization"] == "Bearer test-token-not-a-secret" for c in session.calls)
    assert session.calls[0][2]["data"]["return_images"] == "true"


def test_constructor_query_base_overrides_environment(monkeypatch):
    monkeypatch.setenv("MINERU_TASK_QUERY_URL", BASE + "/different/tasks")
    session = Session()
    client(session, task_query_url=QUERY).parse_image(b"synthetic", "sample.png", ".png")
    assert session.calls[1][1] == QUERY + "/task-1"
    assert session.calls[0][2]["files"]["files"][2] == "image/png"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_or_empty_query_url_preserves_original_contract(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("MINERU_TASK_QUERY_URL", value)
    session = Session()
    client(session).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert session.calls[1][1] == BASE + "/tasks/task-1"


@pytest.mark.parametrize(
    "url",
    [
        "https://outside.invalid/tasks",
        "http://aiapi.cnooc.com.cn/tasks",
        "https://aiapi.cnooc.com.cn:444/tasks",
        "https://aiapi.cnooc.com.cn:0/tasks",
        "https://user:private@aiapi.cnooc.com.cn/tasks",
        "https://aiapi.cnooc.com.cn/tasks?token=private",
        "https://aiapi.cnooc.com.cn/tasks#private",
        "//aiapi.cnooc.com.cn/tasks",
        "/dynamic/tasks",
        "https://aiapi.cnooc.com.cn/task",
        BASE + "/../tasks",
        BASE + "/./tasks",
        BASE + "/%2e%2e/tasks",
        BASE + "//tasks",
        BASE + "/tasks/{task_id}",
        BASE + "/\\private/tasks",
        BASE + "/\nprivate/tasks",
        BASE + "/private space/tasks",
        BASE + "/tasks?",
        BASE + "/tasks#",
    ],
)
def test_invalid_query_config_fails_before_network_without_disclosing_value(monkeypatch, url):
    monkeypatch.setenv("MINERU_TASK_QUERY_URL", url)
    session = Session()
    with pytest.raises(ValueError, match="task query URL") as exc:
        client(session)
    assert url not in str(exc.value)
    assert "private" not in str(exc.value)
    assert session.calls == []


def test_same_origin_default_https_port_is_accepted():
    session = Session()
    query = QUERY.replace("aiapi.cnooc.com.cn", "aiapi.cnooc.com.cn:443")
    client(session, task_query_url=query).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert session.calls[1][1] == query + "/task-1"


def test_old_http_base_cannot_receive_new_https_query_override():
    with pytest.raises(ValueError, match="task query URL"):
        app.EnterpriseMinerUClient(api_url="http://aiapi.cnooc.com.cn/old", api_token="test", task_query_url=QUERY, session=Session())


def test_transient_new_route_polling_retries_only_same_task():
    session = Session(
        statuses=[app.requests.ReadTimeout("synthetic"), Response(404, {}), Response(200, {"data": {"status": "completed"}})],
        results=[Response(200, {"success": False, "code": 20006}), Response(200, {"data": {"results": {"document": {"md_content": "ok"}}}})],
    )
    client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert sum(m == "POST" for m, _, _ in session.calls) == 1
    assert {u for m, u, _ in session.calls if m == "GET"} == {QUERY + "/task-1", QUERY + "/task-1/result"}


@pytest.mark.parametrize("stage", ["submit", "status", "result"])
@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_rejected_without_retry(stage, status):
    redirect = Response(status, {"task_id": "task-1", "status": "completed", "results": {}})
    session = Session(**({"submission": redirect} if stage == "submit" else {"statuses": [redirect]} if stage == "status" else {"results": [redirect]}))
    with pytest.raises(RuntimeError, match="redirect"):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert sum(m == "POST" for m, _, _ in session.calls) == 1
    assert all(c[2]["allow_redirects"] is False for c in session.calls)
    assert len(session.calls) == {"submit": 1, "status": 2, "result": 3}[stage]


def test_submission_timeout_never_retries_or_falls_back():
    session = Session(submission=app.requests.ReadTimeout("synthetic"))
    with pytest.raises(app.requests.ReadTimeout):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert len(session.calls) == 1


def test_query_route_does_not_reset_total_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: clock[0])
    session = Session()
    original = session.post

    def delayed_submission(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] = 31.0
        return result

    session.post = delayed_submission
    with pytest.raises(TimeoutError):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert len(session.calls) == 1


def test_repeated_query_timeout_stops_without_resubmitting():
    session = Session(statuses=[app.requests.ReadTimeout("synthetic") for _ in range(3)])
    with pytest.raises(app.requests.ReadTimeout):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert sum(m == "POST" for m, _, _ in session.calls) == 1
    assert len(session.calls) == 4


@pytest.mark.parametrize("task_id", [".", "..", "../secret", "a/b", "a?token=x", "%2e%2e", ""])
def test_untrusted_task_ids_never_reach_query_route(task_id):
    session = Session(submission=Response(202, {"task_id": task_id}))
    with pytest.raises(RuntimeError, match="invalid task identifier"):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert len(session.calls) == 1


@pytest.mark.parametrize("status", ["failed", "error"])
def test_terminal_failure_never_fetches_result_or_resubmits(status):
    session = Session(statuses=[Response(200, {"status": status, "message": "synthetic failure"})])
    with pytest.raises(RuntimeError):
        client(session, task_query_url=QUERY).parse_pdf(b"%PDF-synthetic", "sample.pdf")
    assert len(session.calls) == 2
