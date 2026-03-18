"""Shared constants used across importer, stats, and data_access modules."""

from __future__ import annotations

GAME_TYPE_SHORT = {
    "Hold'em No Limit": "NL Holdem",
    "Omaha Pot Limit": "PL Omaha",
    "Hold'em Pot Limit": "PL Holdem",
    "Omaha No Limit": "NL Omaha",
}

POSITION_DISPLAY = {
    "BTN": "BTN",
    "SB": "SB",
    "BB": "BB",
    "UTG": "EP",
    "MP": "MP",
    "CO": "CO",
}

POSITION_FILTER_MAP = {
    "BTN": ["BTN"],
    "SB": ["SB"],
    "BB": ["BB"],
    "EP": ["UTG", "EP"],
    "MP": ["MP"],
    "CO": ["CO"],
}

POSITION_ORDER = {"BTN": 0, "CO": 1, "MP": 2, "EP": 3, "SB": 4, "BB": 5}
ALL_POSITIONS = ["BTN", "CO", "MP", "EP", "SB", "BB"]

# ── Donation / support ───────────────────────────────────────────────────────
# Set to your Ko-fi (or similar) public page URL to display the
# "Support Development" section in the sidebar.  Set to an empty string
# to hide the section entirely (e.g. for forks or private installs).
DONATION_URL: str = "https://ko-fi.com/lwashington"

# ── App versioning / updates ─────────────────────────────────────────────────
APP_VERSION: str = "1.0.0"
GITHUB_RELEASES_URL: str = "https://github.com/LWashington11/BetRiversTracker_private/releases"

# ── Import / data thresholds ────────────────────────────────────────────────
MAX_UPLOAD_SIZE: int = 50 * 1024 * 1024  # 50 MB per uploaded file
IMPORT_CHUNK_SIZE: int = 1000            # hands per import transaction
CACHE_TTL: int = 300                     # Streamlit @cache_data TTL (seconds)
CUMULATIVE_MAX_POINTS: int = 5000        # max points for cumulative chart
REPLAYER_LIST_LIMIT: int = 500           # recent hands shown in replayer
HANDS_REPORT_PAGE_SIZE: int = 200        # hands per page in the report grid
