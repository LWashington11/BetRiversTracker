"""
Persistent user preferences — stored in .betrivers_prefs.json at project root.

All functions are safe to call from multiple Streamlit pages.  Reads/writes
are best-effort; errors are silently swallowed to avoid crashing the app.

Keys in use
-----------
``cols_session``    visible/ordered stat columns for the Session Report tab
``cols_stakes``     visible/ordered stat columns for the Results by Stakes tab
``cols_position``   visible/ordered stat columns for the Results by Position tab
``cols_hands``      visible/ordered columns for the Hands Report page
``visible_stats``   legacy key (migrated automatically on first read)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PREFS_PATH = Path(__file__).resolve().parent.parent / ".betrivers_prefs.json"

# Keys that hold stat column selections — eligible for legacy migration.
_STAT_KEYS = {"cols_session", "cols_stakes", "cols_position"}


import logging as _logging
_log = _logging.getLogger(__name__)


def _read() -> dict:
    """Read the full prefs dict, returning {} on any error."""
    try:
        if _PREFS_PATH.exists():
            data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        _log.warning("Failed to read preferences from %s", _PREFS_PATH,
                     exc_info=True)
    return {}


def _write(data: dict) -> None:
    """Persist the full prefs dict to disk (atomic via temp + rename)."""
    try:
        import tempfile
        import os
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=_PREFS_PATH.parent, suffix=".tmp",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, _PREFS_PATH)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        _log.warning("Failed to write preferences to %s", _PREFS_PATH,
                     exc_info=True)


def get_col_setting(key: str, default: list[str]) -> list[str]:
    """Return the stored column list for *key*, falling back to *default*.

    For the three dashboard stat-column keys a one-time migration from the
    legacy ``visible_stats`` key is performed transparently.
    """
    data = _read()
    val = data.get(key)
    if isinstance(val, list) and all(isinstance(v, str) for v in val):
        return val
    # Legacy migration: if this is a stat key and visible_stats exists, reuse it.
    if key in _STAT_KEYS:
        legacy = data.get("visible_stats")
        if isinstance(legacy, list) and all(isinstance(v, str) for v in legacy):
            return legacy
    return list(default)


def save_col_setting(key: str, cols: list[str]) -> None:
    """Persist the column list for *key*, preserving all other prefs."""
    data = _read()
    data[key] = cols
    _write(data)


def get_pref(key: str, default: Any = None) -> Any:
    """Return an arbitrary stored preference, or *default*."""
    return _read().get(key, default)


def save_pref(key: str, value: Any) -> None:
    """Persist an arbitrary preference key=value, preserving all other prefs."""
    data = _read()
    data[key] = value
    _write(data)
