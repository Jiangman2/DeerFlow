"""Avatar pointer failures must not destroy current or legacy employee data."""

import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.persistence.agents import avatars


def test_immutable_assets_are_idempotent_and_explicit_clear_never_falls_back(tmp_path):
    legacy = tmp_path / "avatar.webp"
    legacy.write_bytes(b"legacy")
    assert avatars.personal_avatar_path(tmp_path, {}) == legacy
    assert avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: None}) is None
    content = b"encoded WebP bytes from trusted encoder"
    with ThreadPoolExecutor(max_workers=2) as pool:
        digests = list(pool.map(lambda _: avatars.stage_avatar(tmp_path, content), range(2)))
    assert digests == [hashlib.sha256(content).hexdigest()] * 2
    path = avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: digests[0]})
    assert avatars.read_avatar_bytes(path) == content
    assert legacy.read_bytes() == b"legacy"
    assert list((tmp_path / ".avatar-assets").glob(".avatar-*")) == []
    assert (tmp_path / ".avatar-assets" / "FORMAT-v1").read_bytes() == avatars.AVATAR_FORMAT_MARKER


def test_corrupt_format_marker_is_not_overwritten(tmp_path):
    directory = avatars.mark_avatar_format(tmp_path)
    marker = directory / "FORMAT-v1"
    marker.write_bytes(b"unknown format")
    with pytest.raises(ValueError, match="format marker"):
        avatars.stage_avatar(tmp_path, b"new portrait")
    assert marker.read_bytes() == b"unknown format"
    assert list(directory.glob("*.webp")) == []


@pytest.mark.parametrize("digest", ["../outside", "A" * 64, 123, "", {}, ["a"]])
def test_user_selected_or_malformed_asset_path_is_rejected(tmp_path, digest):
    with pytest.raises(ValueError):
        avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: digest})


def test_corrupt_asset_is_not_silently_replaced_or_published(tmp_path):
    digest = avatars.stage_avatar(tmp_path, b"first")
    path = avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: digest})
    path.write_bytes(b"damaged")
    with pytest.raises(ValueError, match="digest mismatch"):
        avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: digest})
    with pytest.raises(ValueError, match="damaged"):
        avatars.stage_avatar(tmp_path, b"first")
    assert path.read_bytes() == b"damaged"


def test_failed_asset_stage_preserves_previous_and_cleans_own_temp_file(tmp_path, monkeypatch):
    digest = avatars.stage_avatar(tmp_path, b"first")
    original = avatars.personal_avatar_path(tmp_path, {avatars.AVATAR_ASSET_KEY: digest})

    def fail_replace(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(avatars.os, "replace", fail_replace)
    with pytest.raises(OSError):
        avatars.stage_avatar(tmp_path, b"second")
    assert original.read_bytes() == b"first"
    assert list((tmp_path / ".avatar-assets").glob(".avatar-*")) == []


def test_assets_cannot_escape_through_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "employee"
    root.mkdir()
    try:
        (root / ".avatar-assets").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Platform does not permit directory symlinks")
    with pytest.raises(ValueError):
        avatars.stage_avatar(root, b"bytes")
    assert list(outside.iterdir()) == []


def test_oversized_asset_is_rejected_before_write(tmp_path):
    with pytest.raises(ValueError):
        avatars.stage_avatar(tmp_path, b"x" * (avatars.MAX_AVATAR_BYTES + 1))
    assert list(tmp_path.iterdir()) == []
