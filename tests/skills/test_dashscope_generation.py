import json
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
