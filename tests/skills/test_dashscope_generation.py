import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from skill_loader import FakeResp, load  # noqa: E402

image = load("image-generation")
music = load("music-generation")
podcast = load("podcast-generation")
video = load("video-generation")


def test_dashscope_image_generation_downloads_result(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "dashscope")
    captured = {}

    def fake_post(url, headers=None, json=None, **kwargs):
        captured.update(url=url, headers=headers, json=json)
        return FakeResp(
            {
                "output": {
                    "choices": [
                        {"message": {"content": [{"image": "https://files.example/image.png"}]}}
                    ]
                }
            }
        )

    monkeypatch.setattr(image.requests, "post", fake_post)
    monkeypatch.setattr(
        image.requests,
        "get",
        lambda url, **kwargs: FakeResp(content=b"\x89PNG\r\n\x1a\nimage"),
    )
    prompt = tmp_path / "prompt.json"
    prompt.write_text(
        json.dumps({"prompt": "中文企业信息图", "negative_prompt": "模糊"}),
        encoding="utf-8",
    )
    output = tmp_path / "nested" / "image.png"

    result = image.generate_image(str(prompt), [], str(output), "16:9")

    assert output.read_bytes().startswith(b"\x89PNG")
    assert captured["url"].endswith("/services/aigc/multimodal-generation/generation")
    assert captured["json"]["model"] == "qwen-image-2.0-pro"
    assert captured["json"]["parameters"]["size"] == "2688*1536"
    assert captured["json"]["parameters"]["negative_prompt"] == "模糊"
    assert captured["headers"]["Authorization"] == "Bearer dash-key"
    assert "Successfully generated" in result


def test_dashscope_image_tls_compat_uses_scoped_verified_session(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("IMAGE_GENERATION_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_TLS_COMPAT", "true")
    ca_file = tmp_path / "enterprise-ca.pem"
    ca_file.write_text("test-ca", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(ca_file))

    class FakeContext:
        def __init__(self):
            self.ciphers = None

        def set_ciphers(self, ciphers):
            self.ciphers = ciphers

    context = FakeContext()
    context_calls = []
    monkeypatch.setattr(
        image.ssl,
        "create_default_context",
        lambda **kwargs: context_calls.append(kwargs) or context,
    )

    class FakeSession:
        def __init__(self):
            self.mounts = {}
            self.posts = []
            self.gets = []

        def mount(self, prefix, adapter):
            self.mounts[prefix] = adapter

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            return FakeResp(
                {
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "content": [
                                        {"image": "https://files.example/image.png"}
                                    ]
                                }
                            }
                        ]
                    }
                }
            )

        def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return FakeResp(content=b"\x89PNG\r\n\x1a\nimage")

    session = FakeSession()
    monkeypatch.setattr(image.requests, "Session", lambda: session)
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("企业智能油田示意图", encoding="utf-8")
    output = tmp_path / "image.png"

    result = image.generate_image(str(prompt), [], str(output), "16:9")

    assert context_calls == [{"cafile": str(ca_file)}]
    assert context.ciphers == "DEFAULT:@SECLEVEL=1"
    assert isinstance(session.mounts["https://"], image._DashScopeTLSAdapter)
    assert len(session.posts) == 1
    assert len(session.gets) == 1
    assert output.read_bytes().startswith(b"\x89PNG")
    assert "Successfully generated" in result


def test_dashscope_music_generation_downloads_result(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("MUSIC_GENERATION_PROVIDER", "dashscope")
    captured = {}

    def fake_post(url, headers=None, json=None, **kwargs):
        captured.update(url=url, json=json)
        return FakeResp({"output": {"audio": {"url": "https://files.example/music.mp3"}}})

    monkeypatch.setattr(music.requests, "post", fake_post)
    monkeypatch.setattr(
        music.requests,
        "get",
        lambda url, **kwargs: FakeResp(content=b"ID3music"),
    )
    prompt = tmp_path / "music.json"
    prompt.write_text(
        json.dumps({"prompt": "轻快科技感背景音乐", "is_instrumental": True}),
        encoding="utf-8",
    )
    output = tmp_path / "music.mp3"

    result = music.generate_music(str(prompt), str(output))

    assert output.read_bytes() == b"ID3music"
    assert captured["url"].endswith("/services/audio/music/generation")
    assert captured["json"]["model"] == "fun-music-v1"
    assert captured["json"]["input"]["is_instrumental"] is True
    assert captured["json"]["input"]["format"] == "mp3"
    assert "Successfully generated" in result


def test_dashscope_music_permission_error_is_actionable(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("MUSIC_GENERATION_PROVIDER", "dashscope")
    monkeypatch.setattr(
        music.requests,
        "post",
        lambda *args, **kwargs: FakeResp(
            {
                "code": "Forbidden",
                "message": "Model access denied",
                "request_id": "req-403",
            },
            status_code=403,
        ),
    )
    prompt = tmp_path / "music.json"
    prompt.write_text(
        json.dumps({"prompt": "轻快科技感背景音乐", "is_instrumental": True}),
        encoding="utf-8",
    )

    with pytest.raises(PermissionError, match="China \\(Beijing\\).*req-403"):
        music.generate_music(str(prompt), str(tmp_path / "music.mp3"))


def test_dashscope_podcast_uses_qwen_tts_and_voice_mapping(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("PODCAST_GENERATION_PROVIDER", "dashscope")
    requests_seen = []

    def fake_post(url, headers=None, json=None, **kwargs):
        requests_seen.append((url, json))
        return FakeResp({"output": {"audio": {"url": "https://files.example/voice.wav"}}})

    monkeypatch.setattr(podcast.requests, "post", fake_post)
    monkeypatch.setattr(
        podcast.requests,
        "get",
        lambda url, **kwargs: FakeResp(content=b"RIFFvoice"),
    )
    script = tmp_path / "podcast.json"
    script.write_text(
        json.dumps(
            {
                "locale": "zh",
                "lines": [
                    {"speaker": "male", "paragraph": "欢迎收听。"},
                    {"speaker": "female", "paragraph": "今天介绍百炼。"},
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "podcast.wav"

    result = podcast.generate_podcast(str(script), str(output), None)

    assert output.read_bytes() == b"RIFFvoiceRIFFvoice"
    assert {request[1]["input"]["voice"] for request in requests_seen} == {"Ethan", "Cherry"}
    assert all(request[1]["model"] == "qwen3-tts-flash" for request in requests_seen)
    assert all(
        request[0].endswith("/services/aigc/multimodal-generation/generation")
        for request in requests_seen
    )
    assert "Successfully generated podcast" in result


def test_dashscope_video_generation_polls_and_downloads(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "dashscope")
    monkeypatch.delenv("DASHSCOPE_VIDEO_MODEL", raising=False)
    monkeypatch.setattr(video.time, "sleep", lambda *_: None)
    captured = {}

    def fake_post(url, headers=None, json=None, **kwargs):
        captured.update(url=url, headers=headers, json=json)
        return FakeResp({"output": {"task_id": "task-1"}, "request_id": "request-1"})

    def fake_get(url, headers=None, **kwargs):
        if url.endswith("/tasks/task-1"):
            return FakeResp(
                {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "video_url": "https://files.example/video.mp4",
                    }
                }
            )
        return FakeResp(content=b"MP4video")

    monkeypatch.setattr(video.requests, "post", fake_post)
    monkeypatch.setattr(video.requests, "get", fake_get)
    prompt = tmp_path / "video.txt"
    prompt.write_text("海上平台的数字孪生动画", encoding="utf-8")
    output = tmp_path / "video.mp4"

    result = video.generate_video(str(prompt), [], str(output), "16:9")

    assert output.read_bytes() == b"MP4video"
    assert captured["url"].endswith("/services/aigc/video-generation/video-synthesis")
    assert captured["headers"]["X-DashScope-Async"] == "enable"
    assert captured["json"]["model"] == "wan2.7-t2v"
    assert captured["json"]["parameters"]["resolution"] == "720P"
    assert captured["json"]["parameters"]["ratio"] == "16:9"
    assert captured["json"]["parameters"]["duration"] == 5
    assert "successfully" in result.lower()


def test_dashscope_video_submit_only_persists_task_without_polling(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "dashscope")
    calls = {"post": 0, "get": 0}

    def fake_post(url, headers=None, json=None, **kwargs):
        calls["post"] += 1
        return FakeResp(
            {"output": {"task_id": "task-async-1"}, "request_id": "request-async-1"}
        )

    def unexpected_get(*args, **kwargs):
        calls["get"] += 1
        raise AssertionError("submit-only must not poll the asynchronous task")

    monkeypatch.setattr(video.requests, "post", fake_post)
    monkeypatch.setattr(video.requests, "get", unexpected_get)
    prompt = tmp_path / "video.txt"
    prompt.write_text("an offshore drilling platform at sunrise", encoding="utf-8")
    output = tmp_path / "video.mp4"

    result = video.submit_video_task(str(prompt), [], str(output), "16:9")
    state_path = video._video_task_state_path(str(output))
    state = video._load_video_task_state(state_path)

    assert result.startswith("VIDEO_TASK_SUBMITTED")
    assert calls == {"post": 1, "get": 0}
    assert state["provider"] == "dashscope"
    assert state["task_id"] == "task-async-1"
    assert state["status"] == "PENDING"
    assert state["output_file"] == str(output)
    assert "dash-key" not in Path(state_path).read_text(encoding="utf-8")


def test_dashscope_video_async_submit_ignores_stale_provider_override(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "gemini")
    post_calls = 0

    def fake_post(url, headers=None, json=None, **kwargs):
        nonlocal post_calls
        post_calls += 1
        return FakeResp({"output": {"task_id": "task-pinned"}})

    monkeypatch.setattr(video.requests, "post", fake_post)
    prompt = tmp_path / "video.txt"
    prompt.write_text("an offshore platform", encoding="utf-8")
    output = tmp_path / "video.mp4"

    result = video.submit_video_task(str(prompt), [], str(output), "16:9")

    assert result.startswith("VIDEO_TASK_SUBMITTED")
    assert post_calls == 1
    state_path = video._video_task_state_path(str(output))
    assert video._load_video_task_state(state_path)["provider"] == "dashscope"


def test_video_skill_is_provider_opaque_and_dashscope_only():
    skill_text = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "public"
        / "video-generation"
        / "SKILL.md"
    ).read_text(encoding="utf-8")

    normalized = " ".join(skill_text.split())
    assert "--submit-only" in normalized
    assert "--status-only" in normalized
    assert "Do not inspect the script" in normalized
    assert "enumerate environment variables" in normalized
    assert "construct a direct HTTP request" in normalized
    assert "Do not try" in normalized and "Gemini" in normalized
    assert "GEMINI_API_KEY" not in skill_text
    assert "MINIMAX_API_KEY" not in skill_text
    assert "requests.post" not in skill_text


def test_video_cli_rejects_foreground_mode(tmp_path):
    prompt = tmp_path / "video.json"
    prompt.write_text('{"prompt":"offshore platform"}', encoding="utf-8")
    output = tmp_path / "video.mp4"
    script = (
        Path(__file__).resolve().parents[2]
        / "skills"
        / "public"
        / "video-generation"
        / "scripts"
        / "generate.py"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--prompt-file",
            str(prompt),
            "--output-file",
            str(output),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode != 0
    assert "--submit-only or --status-only is required" in completed.stderr
    assert not output.exists()


def test_dashscope_video_submit_only_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("VIDEO_GENERATION_PROVIDER", "dashscope")
    prompt = tmp_path / "video.txt"
    prompt.write_text("a platform", encoding="utf-8")
    output = tmp_path / "video.mp4"
    post_calls = 0

    def fake_post(url, headers=None, json=None, **kwargs):
        nonlocal post_calls
        post_calls += 1
        return FakeResp({"output": {"task_id": "task-once"}})

    monkeypatch.setattr(video.requests, "post", fake_post)

    first = video.submit_video_task(str(prompt), [], str(output), "16:9")
    second = video.submit_video_task(str(prompt), [], str(output), "16:9")

    assert first.startswith("VIDEO_TASK_SUBMITTED")
    assert second.startswith("VIDEO_TASK_ALREADY_SUBMITTED")
    assert post_calls == 1


def test_dashscope_video_status_checks_once_without_sleeping(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    output = tmp_path / "video.mp4"
    state_path = video._video_task_state_path(str(output))
    video._write_video_task_state(
        state_path,
        {
            "version": 1,
            "provider": "dashscope",
            "task_id": "task-pending",
            "status": "PENDING",
            "output_file": str(output),
        },
    )
    get_calls = 0

    def fake_get(url, headers=None, **kwargs):
        nonlocal get_calls
        get_calls += 1
        return FakeResp({"output": {"task_status": "RUNNING"}})

    monkeypatch.setattr(video.requests, "get", fake_get)
    monkeypatch.setattr(
        video.time,
        "sleep",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("status-only must never contain a polling sleep")
        ),
    )

    result = video.check_video_task(str(output))

    assert result.startswith("VIDEO_TASK_PENDING")
    assert get_calls == 1
    assert video._load_video_task_state(state_path)["status"] == "RUNNING"


def test_dashscope_video_status_downloads_completed_task(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    output = tmp_path / "nested" / "video.mp4"
    state_path = video._video_task_state_path(str(output))
    video._write_video_task_state(
        state_path,
        {
            "version": 1,
            "provider": "dashscope",
            "task_id": "task-done",
            "status": "RUNNING",
            "output_file": str(output),
        },
    )

    def fake_get(url, headers=None, **kwargs):
        if url.endswith("/tasks/task-done"):
            return FakeResp(
                {
                    "output": {
                        "task_status": "SUCCEEDED",
                        "video_url": "https://files.example/video.mp4",
                    }
                }
            )
        return FakeResp(content=b"MP4async")

    monkeypatch.setattr(video.requests, "get", fake_get)

    result = video.check_video_task(str(output))

    assert result.startswith("VIDEO_TASK_SUCCEEDED")
    assert output.read_bytes() == b"MP4async"
    state = video._load_video_task_state(state_path)
    assert state["status"] == "SUCCEEDED"
    assert "video_url" not in state


def test_dashscope_video_status_records_terminal_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    output = tmp_path / "video.mp4"
    state_path = video._video_task_state_path(str(output))
    video._write_video_task_state(
        state_path,
        {
            "version": 1,
            "provider": "dashscope",
            "task_id": "task-failed",
            "status": "RUNNING",
            "output_file": str(output),
        },
    )
    monkeypatch.setattr(
        video.requests,
        "get",
        lambda *args, **kwargs: FakeResp(
            {
                "output": {
                    "task_status": "FAILED",
                    "code": "InvalidParameter",
                    "message": "bad prompt",
                }
            }
        ),
    )

    with pytest.raises(Exception, match="task task-failed failed"):
        video.check_video_task(str(output))

    state = video._load_video_task_state(state_path)
    assert state["status"] == "FAILED"
    assert state["error_code"] == "InvalidParameter"
