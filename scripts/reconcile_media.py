#!/usr/bin/env python3
"""Audit or explicitly remove old media files without database owners."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.config import Settings
from app.db import Database
from app.storage import find_orphan_media, resolve_stored_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--delete",
        action="store_true",
        help="remove discovered files after ownership and age checks; default is report-only",
    )
    parser.add_argument(
        "--older-than-hours",
        type=int,
        default=24,
        help="only consider files older than this many hours (default: 24)",
    )
    args = parser.parse_args()
    if args.older_than_hours < 1:
        parser.error("--older-than-hours must be at least 1")

    settings = Settings.from_env()
    database = Database(settings.db_path)
    database.initialize()
    referenced_paths = set()
    for raw_path in database.list_media_paths():
        try:
            referenced_paths.add(
                resolve_stored_path(raw_path, settings.data_dir, settings.uploads_dir, settings.images_dir)
            )
        except ValueError:
            print(f"unsafe stored path skipped: {raw_path}")
    orphaned = find_orphan_media(
        (settings.uploads_dir, settings.images_dir),
        referenced_paths,
        older_than_seconds=args.older_than_hours * 3600,
    )
    if not orphaned:
        print("No old unreferenced media files found.")
        return 0
    for path in orphaned:
        if args.delete:
            path.unlink(missing_ok=True)
            print(f"removed {path}")
        else:
            print(f"unreferenced {path}")
    if not args.delete:
        print("Report only; rerun with --delete after reviewing the paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
