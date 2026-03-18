"""
Persistent hero-name storage.

Maintains a JSON file of previously used hero names so users can quickly
re-select them.  The file is stored alongside the app package:

    <project_root>/.hero_names.json

Schema::

    {
        "names": ["hero", "other_player"],
        "last_used": "hero"
    }

All public functions are safe to call from multiple Streamlit pages —
they read/write the file atomically and never raise on I/O errors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

_STORE_PATH = Path(__file__).resolve().parent.parent / ".hero_names.json"


import logging as _logging
_log = _logging.getLogger(__name__)


def _read_store() -> dict:
    """Read the JSON store, returning a safe default on any error."""
    try:
        if _STORE_PATH.exists():
            data = json.loads(_STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("names"), list):
                return data
    except Exception:
        _log.warning("Failed to read hero store from %s", _STORE_PATH,
                     exc_info=True)
    return {"names": [], "last_used": None}


def _write_store(data: dict) -> None:
    """Persist the store dict to disk (atomic via temp + rename)."""
    try:
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_STORE_PATH.parent, suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, _STORE_PATH)
        except BaseException:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        _log.warning("Failed to write hero store to %s", _STORE_PATH,
                     exc_info=True)


def get_hero_names() -> list[str]:
    """Return all previously stored hero names (most recent first)."""
    return list(_read_store().get("names", []))


def get_last_hero() -> Optional[str]:
    """Return the last-used hero name, or None if never set."""
    return _read_store().get("last_used")


def save_hero(name: str) -> None:
    """
    Record *name* as the active hero.

    Adds it to the list (if new) and marks it as ``last_used``.
    """
    name = name.strip()
    if not name:
        return
    store = _read_store()
    names: list[str] = store.get("names", [])
    # Move to front if already present, otherwise prepend
    if name in names:
        names.remove(name)
    names.insert(0, name)
    store["names"] = names
    store["last_used"] = name
    _write_store(store)


def remove_hero(name: str) -> None:
    """Remove a hero name from the stored list."""
    store = _read_store()
    names: list[str] = store.get("names", [])
    if name in names:
        names.remove(name)
    store["names"] = names
    if store.get("last_used") == name:
        store["last_used"] = names[0] if names else None
    _write_store(store)
