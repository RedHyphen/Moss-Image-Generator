import io
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app.storage import (
    contained_path,
    find_orphan_media,
    media_key,
    media_usage_bytes,
    resolve_stored_path,
    safe_display_filename,
    save_upload,
)


class FakeUpload:
    filename = "test.png"

    def __init__(self, payload):
        self.payload = payload

    async def read(self, limit):
        return self.payload[:limit]


@pytest.mark.asyncio
async def test_upload_is_normalized_to_png():
    buffer = io.BytesIO()
    Image.new("RGB", (32, 16), "red").save(buffer, format="JPEG")
    with tempfile.TemporaryDirectory() as directory:
        result = await save_upload(FakeUpload(buffer.getvalue()), Path(directory), 1024 * 1024, 10_000)
        assert result["path"].suffix == ".png"
        with Image.open(result["path"]) as image:
            assert image.format == "PNG"
            assert image.size == (32, 16)


@pytest.mark.asyncio
async def test_upload_rejects_oversize_file():
    with pytest.raises(HTTPException) as error:
        await save_upload(FakeUpload(b"12345"), Path(tempfile.mkdtemp()), 4, 10_000)
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_rejects_oversize_dimensions_before_decode():
    buffer = io.BytesIO()
    Image.new("RGB", (101, 101), "red").save(buffer, format="PNG", optimize=True)
    with pytest.raises(HTTPException) as error:
        await save_upload(FakeUpload(buffer.getvalue()), Path(tempfile.mkdtemp()), 1024 * 1024, 10_000)
    assert error.value.status_code == 413


def test_contained_path_rejects_escape(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    assert contained_path(root / "ok.png", root) == (root / "ok.png").resolve()
    with pytest.raises(ValueError):
        contained_path(root / ".." / "secret.txt", root)


def test_media_keys_are_portable_and_legacy_absolute_paths_still_resolve(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    output = images / "output.png"
    output.write_bytes(b"png")

    assert media_key(output, tmp_path) == "images/output.png"
    assert resolve_stored_path("images/output.png", tmp_path, images) == output.resolve()
    assert resolve_stored_path(str(output), tmp_path, images) == output.resolve()


def test_display_filename_removes_path_and_control_characters():
    assert safe_display_filename('../draft\r\n"image".png') == "_draft_image_.png"
    assert len(safe_display_filename("x" * 300)) == 200


def test_media_usage_counts_regular_files_once_and_ignores_symlinks(tmp_path):
    uploads = tmp_path / "uploads"
    images = tmp_path / "images"
    uploads.mkdir()
    images.mkdir()
    (uploads / "source.png").write_bytes(b"1234")
    (images / "output.png").write_bytes(b"123456")
    (images / "outside-link").symlink_to(uploads / "source.png")

    assert media_usage_bytes(uploads, images, uploads) == 10


def test_find_orphan_media_is_age_and_owner_aware(tmp_path):
    media = tmp_path / "images"
    media.mkdir()
    owned = media / "owned.png"
    orphan = media / "orphan.png"
    recent = media / "recent.png"
    for path in (owned, orphan, recent):
        path.write_bytes(b"png")
    old_time = 1_000_000
    import os

    os.utime(owned, (old_time, old_time))
    os.utime(orphan, (old_time, old_time))
    os.utime(recent, (old_time + 3_601, old_time + 3_601))

    assert find_orphan_media((media,), {owned}, older_than_seconds=3_600, now=old_time + 7_200) == [orphan.resolve()]
