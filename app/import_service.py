"""
Import Service — orchestrates parsing and importing hand history files.

Extracts the non-UI logic from the dashboard's import dialog so it can
be reused by the CLI and tested independently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.config import HAND_HISTORY_DIR
from app.constants import MAX_UPLOAD_SIZE
from app.parser import parse_file
from app.importer import import_hands


def parse_uploaded_files(
    files: list[tuple[str, bytes]],
    *,
    on_skip: Callable[[str, str], None] | None = None,
) -> list[dict]:
    """
    Parse uploaded hand history files.

    Parameters
    ----------
    files : list of (filename, raw_bytes) tuples
    on_skip : optional callback(filename, reason) for skipped files

    Returns
    -------
    list of parsed hand dicts
    """
    all_parsed: list[dict] = []
    for name, data in files:
        if len(data) > MAX_UPLOAD_SIZE:
            if on_skip:
                on_skip(name, f"file too large ({len(data) / 1024 / 1024:.0f} MB, max 50 MB)")
            continue
        raw_text = data.decode("utf-8", errors="replace")
        safe_name = Path(name).name
        dest_dir = Path(HAND_HISTORY_DIR)
        dest_dir.mkdir(parents=True, exist_ok=True)
        import tempfile
        fd, tmp_str = tempfile.mkstemp(
            suffix=".txt", prefix=f"{Path(safe_name).stem}_",
            dir=dest_dir,
        )
        tmp_path = Path(tmp_str)
        import os as _os
        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw_text)
        all_parsed.extend(parse_file(tmp_path))
    return all_parsed


def parse_directory_safe(
    dir_path: str | Path,
    *,
    on_error: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Parse all .txt hand history files in a directory tree.

    Parameters
    ----------
    dir_path : path to the directory
    on_error : optional callback(filename) for files that fail to parse

    Returns
    -------
    list of parsed hand dicts

    Raises
    ------
    FileNotFoundError
        If the directory does not exist.
    """
    dp = Path(dir_path).resolve()
    if not dp.is_dir():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    all_parsed: list[dict] = []
    for f in sorted(dp.rglob("*.txt")):
        try:
            all_parsed.extend(parse_file(f))
        except Exception:
            if on_error:
                on_error(f.name)
    return all_parsed


def run_import(
    parsed_hands: list[dict],
    *,
    hero_name: str | None = None,
    disable_indexes: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """
    Import parsed hands into the database.

    Thin wrapper around importer.import_hands for a consistent service API.

    Returns (imported_count, skipped_duplicates).
    """
    if not parsed_hands:
        return 0, 0
    return import_hands(
        parsed_hands,
        hero_name=hero_name,
        disable_indexes=disable_indexes,
        progress_callback=progress_callback,
    )
