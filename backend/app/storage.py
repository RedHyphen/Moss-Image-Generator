from __future__ import annotations

import io
import time
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = False


ALLOWED_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


def safe_display_filename(filename: str | None) -> str:
    """Return a bounded display name with no path/control/header characters."""
    raw = filename or "upload"
    safe = "".join(
        character if (character.isalnum() or character in " ._-()[]") else "_"
        for character in raw
        if ord(character) >= 32 and ord(character) != 127
    )
    return (safe.strip(" .")[:200] or "upload")


def contained_path(path: Path, *roots: Path) -> Path:
    """Resolve a stored path and reject anything outside approved storage roots."""
    try:
        resolved = path.resolve(strict=False)
        allowed = [root.resolve(strict=False) for root in roots]
    except OSError as exc:
        raise ValueError(f"could not resolve stored path: {exc}") from exc
    if not any(resolved == root or root in resolved.parents for root in allowed):
        raise ValueError("stored path is outside the configured storage roots")
    return resolved


def media_key(path: Path, data_root: Path) -> str:
    """Return a portable, storage-relative key for a media file."""
    resolved = path.resolve(strict=False)
    root = data_root.resolve(strict=False)
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("media path is outside the configured data root") from exc


def resolve_stored_path(raw_path: str | Path, data_root: Path, *roots: Path) -> Path:
    """Resolve new relative media keys and legacy absolute paths safely."""
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = data_root / candidate
    return contained_path(candidate, *roots)


def media_usage_bytes(*roots: Path) -> int:
    """Return regular-file bytes under media roots without following symlinks."""
    total = 0
    seen: set[Path] = set()
    for root in roots:
        try:
            resolved_root = root.resolve(strict=False)
            if not resolved_root.is_dir():
                continue
            candidates = resolved_root.rglob("*")
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved in seen:
                    continue
                seen.add(resolved)
                total += resolved.stat().st_size
            except OSError:
                continue
    return total


def find_orphan_media(
    roots: tuple[Path, ...],
    referenced_paths: set[Path],
    *,
    older_than_seconds: int = 86_400,
    now: float | None = None,
) -> list[Path]:
    """Find old regular files under media roots that have no database owner."""
    cutoff = (time.time() if now is None else now) - max(0, older_than_seconds)
    known = {path.resolve(strict=False) for path in referenced_paths}
    media_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".part", ".tmp"}
    orphaned: list[Path] = []
    for root in roots:
        try:
            candidates = root.resolve(strict=False).rglob("*")
        except OSError:
            continue
        for candidate in candidates:
            try:
                if candidate.is_symlink() or not candidate.is_file() or candidate.suffix.lower() not in media_suffixes:
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved in known or candidate.stat().st_mtime > cutoff:
                    continue
                orphaned.append(resolved)
            except OSError:
                continue
    return sorted(set(orphaned))


async def save_upload(upload: UploadFile, destination: Path, max_bytes: int, max_pixels: int) -> dict:
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(413, "Image exceeds the configured upload size limit")
    try:
        with Image.open(io.BytesIO(content)) as source:
            # Inspect the header dimensions before verification/decoding. This
            # bounds decompression work for highly compressed oversized rasters.
            if source.width * source.height > max_pixels:
                raise HTTPException(413, "Image exceeds the configured pixel limit")
            source.verify()
        with Image.open(io.BytesIO(content)) as source:
            if source.format not in ALLOWED_FORMATS:
                raise HTTPException(415, "Only PNG, JPEG, and WebP images are supported")
            image = source.convert("RGBA" if "A" in source.getbands() else "RGB")
            image_id = str(uuid.uuid4())
            path = destination / f"{image_id}.png"
            image.save(path, format="PNG", optimize=True)
            return {
                "id": image_id,
                "path": path,
                "filename": safe_display_filename(upload.filename),
                "width": source.width,
                "height": source.height,
                "bytes": len(content),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Invalid image upload: {exc}") from exc
