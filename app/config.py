"""Application configuration."""

import os
from pathlib import Path
from urllib.parse import quote_plus


# ── Locate and parse .env ────────────────────────────────────────────────────
# Use a zero-dependency parser so the password is read even if python-dotenv
# silently fails (encoding issue, path-resolution edge case, stale bytecode…).

_env_path = Path(__file__).resolve().parent.parent / ".env"


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Read a .env file into a plain dict without any library dependency."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    # utf-8-sig strips a BOM if present; works fine without one too.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, val = stripped.partition("=")
        if sep:
            values[key.strip()] = val.strip()
    return values


_dotenv = _parse_dotenv(_env_path)

# Also push values into os.environ via python-dotenv (best-effort).
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path, override=True)
except Exception:
    pass


def _cfg(key: str, default: str = "") -> str:
    """Return config: os.environ > manual .env parse > default."""
    return os.getenv(key) or _dotenv.get(key) or default


# ── PostgreSQL connection ────────────────────────────────────────────────────
DB_USER = _cfg("PGUSER", "postgres")
DB_PASSWORD = _cfg("PGPASSWORD", "postgres")
DB_HOST = _cfg("PGHOST", "localhost")
DB_PORT = _cfg("PGPORT", "5432")
DB_NAME = _cfg("PGDATABASE", "betrivers_tracker")

DATABASE_URL = (
    f"postgresql://{quote_plus(DB_USER)}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# Emit a one-line diagnostic so Streamlit / the console shows what
# config was resolved.  Mask the password entirely for safety.
import logging as _logging
_logging.getLogger(__name__).debug(
    ".env=%s  exists=%s  host=%s  user=%s  password=***  db=%s",
    _env_path, _env_path.is_file(), DB_HOST, DB_USER, DB_NAME,
)

# Directory to watch / import hand histories from.
# If the env value is a relative path, resolve it against the project root
# so it works regardless of the process working directory.
_PROJECT_ROOT = _env_path.parent
_raw_hand_dir = _cfg("HAND_HISTORY_DIR")
if _raw_hand_dir:
    _p = Path(_raw_hand_dir)
    HAND_HISTORY_DIR = str(_p if _p.is_absolute() else _PROJECT_ROOT / _p)
else:
    HAND_HISTORY_DIR = str(_PROJECT_ROOT / "hand_histories")
