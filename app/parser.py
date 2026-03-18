"""
BetRivers Poker hand-history parser.

Parses raw .txt hand-history files produced by BetRivers Poker and returns
structured dicts ready for DB insertion.  Supports Splash-the-Pot (STP).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


# ── Compiled Regexes ─────────────────────────────────────────────────────────

# Header line
RE_HEADER = re.compile(
    r"BetRivers Poker Hand #(?P<hand_id>\d+):\s+"
    r"(?P<game_type>[^(]+)\s+"
    r"\(\$(?P<sb>[\d.]+)/\$(?P<bb>[\d.]+)\)\s+-\s+"
    r"(?P<datetime>\d{4}/\d{2}/\d{2}\s+\d{1,2}:\d{2})\s+(?P<tz>\w+)"
)

# Table info
RE_TABLE = re.compile(
    r"Table ID '(?P<table_id>\d+)'\s+(?P<max_seats>\d+)-Max\s+Seat\s+#(?P<button>\d+)\s+is the button"
)

# Seat line
RE_SEAT = re.compile(
    r"Seat\s+(?P<seat>\d+):\s+(?P<name>.+?)\s+"
    r"\(\$(?P<stack>[\d.]+)\s+in chips\)"
    r"(?P<sitting_out>\s+is sitting out)?"
)

# Blind postings
RE_POST_BLIND = re.compile(
    r"^(?P<name>.+?):\s+posts\s+(?P<blind_type>small blind|big blind|ante)\s+\$(?P<amount>[\d.]+)",
    re.MULTILINE,
)

# STP Added line (appears right after *** HOLE CARDS ***)
RE_STP_ADDED = re.compile(r"^STP Added:\s+\$(?P<amount>[\d.]+)", re.MULTILINE)

# Dealt hole cards
RE_DEALT = re.compile(
    r"^Dealt to (?P<name>.+?)\s+\[(?P<cards>[^\]]+)\]", re.MULTILINE
)

# Street markers
RE_STREET = re.compile(
    r"^\*\*\*\s+(?P<street>HOLE CARDS|FLOP|TURN|RIVER|SHOWDOWN|SUMMARY)\s+\*\*\*"
    r"(?:\s+\[(?P<board>[^\]]+)\])?",
    re.MULTILINE,
)

# Player actions (fold, check, call, bet, raise)
RE_ACTION = re.compile(
    r"^(?P<name>.+?):\s+"
    r"(?P<action>folds|checks|calls|bets|raises)"
    r"(?:\s+\$(?P<amount>[\d.]+))?"
    r"(?:\s+to\s+\$(?P<raise_to>[\d.]+))?"
    r"(?P<allin>\s+and is all-in)?",
    re.MULTILINE,
)

# Collected from pot (handles main pot, side pot, and plain pot)
RE_COLLECTED = re.compile(
    r"^(?P<name>.+?)\s+collected\s+\$(?P<amount>[\d.]+)\s+from\s+(?:(?:main|side|Side)\s+)?pot",
    re.MULTILINE,
)

# Uncalled bet returned
RE_UNCALLED = re.compile(
    r"^Uncalled bet \(\$(?P<amount>[\d.]+)\) returned to (?P<name>.+)",
    re.MULTILINE,
)

# Summary: Total pot line
RE_SUMMARY_POT = re.compile(
    r"Total pot\s+\$(?P<total>[\d.]+)"
    r"(?:\s+\|\s+Main pot\s+\$(?P<main>[\d.]+))?"
    r"(?:\s+\|\s+STP\s+\$(?P<stp>[\d.]+))?"
    r"(?:\s+\|\s+Rake\s+\$(?P<rake>[\d.]+))?",
)

# Summary seat lines — use seat number to look up player instead of
# relying on name capture (which breaks for multi-word names).
RE_SUMMARY_SEAT = re.compile(
    r"^Seat\s+(?P<seat>\d+):\s+(?P<rest>.*)",
    re.MULTILINE,
)

# Board in summary
RE_BOARD = re.compile(r"Board\s+\[(?P<board>[^\]]+)\]")

# Shows hand in showdown section
RE_SHOWS = re.compile(
    r"^(?P<name>.+?)\s+shows\s+\[(?P<cards>[^\]]+)\]", re.MULTILINE
)

# Mucks hand in showdown section
RE_MUCKS = re.compile(
    r"^(?P<name>.+?)\s+mucks hand", re.MULTILINE
)

# Won with amount in summary seat line
RE_SEAT_WON = re.compile(r"won\s+\$(?P<amount>[\d.]+)")


# ── Helpers ──────────────────────────────────────────────────────────────────

# Control-character and zero-width character stripper for player names.
import unicodedata
_CTRL_CHARS = set(chr(c) for c in range(32)) | {chr(127)}


def _sanitize_name(raw_name: str) -> str:
    """Strip control characters and enforce max length on player names."""
    name = raw_name.strip()
    name = "".join(ch for ch in name if ch not in _CTRL_CHARS)
    # Remove Unicode category 'Cf' (format chars like zero-width spaces)
    name = "".join(
        ch for ch in name
        if unicodedata.category(ch) != "Cf"
    )
    # Enforce DB column limit (VARCHAR 128)
    return name[:128]


def _dec(val: str | None) -> Decimal:
    if val is None:
        return Decimal("0")
    try:
        return Decimal(val)
    except InvalidOperation:
        return Decimal("0")


def _parse_datetime(s: str) -> datetime:
    """Parse '2026/02/06 10:09' → datetime."""
    return datetime.strptime(s.strip(), "%Y/%m/%d %H:%M")


POSITION_MAP_6MAX = {
    # relative to button (seat offset → label)
    0: "BTN",
    1: "SB",
    2: "BB",
    3: "UTG",
    4: "MP",  # (or LJ in some notations)
    5: "CO",
}


def _assign_positions(seats: dict[int, dict], button_seat: int, max_seats: int) -> None:
    """Assign positional labels relative to the button for active players."""
    active_seats = sorted(
        s for s, info in seats.items() if not info.get("is_sitting_out")
    )
    if not active_seats:
        return

    # find index of button in the active seats list
    try:
        btn_idx = active_seats.index(button_seat)
    except ValueError:
        # button might be sitting out; pick closest clockwise
        btn_idx = 0

    n = len(active_seats)
    for offset in range(n):
        seat_num = active_seats[(btn_idx + offset) % n]
        # For short-handed, recalc label
        if max_seats <= 6:
            label = POSITION_MAP_6MAX.get(offset, f"S{offset}")
        else:
            label = f"S{offset}"
        seats[seat_num]["position"] = label


def _reconcile_uncalled(
    seats: dict[int, dict],
    actions: list[dict],
    hand: dict[str, Any],
) -> None:
    """Reconcile uncalled bets using the Summary total-pot figure.

    BetRivers hand histories sometimes omit the ``Uncalled bet``
    return line.  The Summary ``Total pot`` always equals the real
    money committed.  Any surplus in our computed investments is an
    uncalled bet that was silently returned to the last aggressor.
    """
    total_pot = hand.get("total_pot") or Decimal("0")
    if total_pot <= 0:
        return

    stp = hand.get("stp_amount") or Decimal("0")
    expected = total_pot - stp

    computed = sum(
        si["total_invested"]
        for si in seats.values()
        if not si.get("is_sitting_out")
    )

    excess = computed - expected
    if excess <= Decimal("0.01"):
        return

    # Build name → seat_info lookup.
    name_to_si: dict[str, dict] = {
        si["name"]: si for si in seats.values()
    }

    # Find the last aggressor (bet / raise) from the action list.
    last_aggressor: str | None = None
    for act in reversed(actions):
        if act["action_type"] in ("bet", "raise"):
            last_aggressor = act["player_name"]
            break

    if last_aggressor and last_aggressor in name_to_si:
        si = name_to_si[last_aggressor]
        si["total_invested"] = max(
            si["total_invested"] - excess, Decimal("0")
        )


# ── Main Parse Function ──────────────────────────────────────────────────────

def parse_hand(raw: str) -> dict[str, Any] | None:
    """
    Parse a single hand history block and return a structured dict.

    Returns None if the hand cannot be parsed (e.g. malformed header).
    """

    # ── Header ───────────────────────────────────────────────────────────
    m = RE_HEADER.search(raw)
    if not m:
        return None

    hand: dict[str, Any] = {
        "hand_id": int(m.group("hand_id")),
        "game_type": m.group("game_type").strip(),
        "small_blind": _dec(m.group("sb")),
        "big_blind": _dec(m.group("bb")),
        "played_at": _parse_datetime(m.group("datetime")),
    }
    hand["played_date"] = hand["played_at"].date()

    # ── Table info ───────────────────────────────────────────────────────
    m = RE_TABLE.search(raw)
    if m:
        hand["table_id"] = m.group("table_id")
        hand["max_seats"] = int(m.group("max_seats"))
        hand["button_seat"] = int(m.group("button"))
    else:
        hand["table_id"] = None
        hand["max_seats"] = 6
        hand["button_seat"] = 1

    # ── Seats / Players ──────────────────────────────────────────────────
    seats: dict[int, dict] = {}
    for m in RE_SEAT.finditer(raw):
        seat_num = int(m.group("seat"))
        seats[seat_num] = {
            "name": _sanitize_name(m.group("name")),
            "stack": _dec(m.group("stack")),
            "is_sitting_out": bool(m.group("sitting_out")),
            "hole_cards": None,
            "won_amount": Decimal("0"),
            "total_invested": Decimal("0"),
            "showed_hand": False,
            "went_to_showdown": False,
            "position": None,
        }

    _assign_positions(seats, hand["button_seat"], hand["max_seats"])
    hand["seats"] = seats

    # ── Name → seat_info lookup for O(1) access ─────────────────────────
    name_to_seat: dict[str, dict] = {
        info["name"]: info for info in seats.values()
    }

    # ── Blind postings ───────────────────────────────────────────────────
    blinds_invested: dict[str, Decimal] = {}
    for m in RE_POST_BLIND.finditer(raw):
        name = m.group("name").strip()
        amt = _dec(m.group("amount"))
        blinds_invested[name] = blinds_invested.get(name, Decimal("0")) + amt

    # Apply blinds to total_invested
    for pname, amt in blinds_invested.items():
        si = name_to_seat.get(pname)
        if si:
            si["total_invested"] += amt

    # ── STP ──────────────────────────────────────────────────────────────
    stp_match = RE_STP_ADDED.search(raw)
    hand["stp_amount"] = _dec(stp_match.group("amount")) if stp_match else Decimal("0")

    # ── Hole cards ───────────────────────────────────────────────────────
    for m in RE_DEALT.finditer(raw):
        name = m.group("name").strip()
        cards = m.group("cards").strip()
        si = name_to_seat.get(name)
        if si:
            si["hole_cards"] = cards

    # ── Street boundaries ────────────────────────────────────────────────
    street_spans: list[tuple[str, int, int]] = []
    street_matches = list(RE_STREET.finditer(raw))
    for i, sm in enumerate(street_matches):
        street_name = sm.group("street")
        start = sm.end()
        end = street_matches[i + 1].start() if i + 1 < len(street_matches) else len(raw)
        street_spans.append((street_name, start, end))

    # ── Board ────────────────────────────────────────────────────────────
    board_match = RE_BOARD.search(raw)
    hand["board"] = board_match.group("board").strip() if board_match else None

    # ── Actions ──────────────────────────────────────────────────────────
    actions: list[dict] = []
    seq = 0
    street_label_map = {
        "HOLE CARDS": "PREFLOP",
        "FLOP": "FLOP",
        "TURN": "TURN",
        "RIVER": "RIVER",
    }

    for street_name, start, end in street_spans:
        label = street_label_map.get(street_name)
        if label is None:
            continue
        segment = raw[start:end]
        for am in RE_ACTION.finditer(segment):
            action_name = am.group("name").strip()
            action_type_raw = am.group("action").rstrip("s")  # folds→fold, etc.
            # normalise
            action_type = {
                "fold": "fold",
                "check": "check",
                "call": "call",
                "bet": "bet",
                "raise": "raise",
            }.get(action_type_raw, action_type_raw)

            amount = _dec(am.group("amount"))
            raise_to = _dec(am.group("raise_to")) if am.group("raise_to") else None
            is_all_in = bool(am.group("allin"))

            actions.append({
                "player_name": action_name,
                "street": label,
                "sequence": seq,
                "action_type": action_type,
                "amount": amount,
                "raise_to": raise_to,
                "is_all_in": is_all_in,
            })
            seq += 1

    hand["actions"] = actions

    # ── Track total invested per player ────────────────────────────────
    # Street commitment tracks the TOTAL amount a player has put into each
    # street.  For preflop, initialise from blind posts so that calls
    # (which report incremental amounts) accumulate correctly.
    street_commitment: dict[str, dict[str, Decimal]] = {}

    # Seed preflop commitments with blind amounts
    street_commitment["PREFLOP"] = {k: v for k, v in blinds_invested.items()}

    for act in actions:
        name = act["player_name"]
        street = act["street"]
        if street not in street_commitment:
            street_commitment[street] = {}
        current = street_commitment[street].get(name, Decimal("0"))

        if act["action_type"] == "call":
            # Call amount is always the ADDITIONAL money put in
            street_commitment[street][name] = current + act["amount"]
        elif act["action_type"] == "bet":
            street_commitment[street][name] = current + act["amount"]
        elif act["action_type"] == "raise":
            if act["raise_to"] is not None:
                # raise_to is the TOTAL commitment for this street
                street_commitment[street][name] = act["raise_to"]
            else:
                street_commitment[street][name] = current + act["amount"]

    # Now total_invested = sum of commitments across all streets
    for seat_info in seats.values():
        name = seat_info["name"]
        total = Decimal("0")
        for street, commits in street_commitment.items():
            total += commits.get(name, Decimal("0"))
        seat_info["total_invested"] = total

    # ── Showdown & Collected ─────────────────────────────────────────────
    # Detect who showed and who mucked at showdown
    showdown_participants: set[str] = set()
    for m in RE_SHOWS.finditer(raw):
        name = m.group("name").strip()
        si = name_to_seat.get(name)
        if si:
            si["showed_hand"] = True
            showdown_participants.add(name)
    for m in RE_MUCKS.finditer(raw):
        name = m.group("name").strip()
        if name in name_to_seat:
            showdown_participants.add(name)

    # A real showdown requires 2+ players showing or mucking
    if len(showdown_participants) >= 2:
        for name in showdown_participants:
            si = name_to_seat.get(name)
            if si:
                si["went_to_showdown"] = True

    # Collected (may appear multiple times for split/side pots)
    for m in RE_COLLECTED.finditer(raw):
        name = m.group("name").strip()
        amt = _dec(m.group("amount"))
        si = name_to_seat.get(name)
        if si:
            si["won_amount"] += amt

    # Uncalled bet returned — this money comes back to the player and
    # should be subtracted from their investment (they didn't really risk it).
    for m in RE_UNCALLED.finditer(raw):
        name = m.group("name").strip()
        amt = _dec(m.group("amount"))
        si = name_to_seat.get(name)
        if si:
            si["total_invested"] -= amt

    # Parse summary seat lines for won amounts and showdown flags.
    # Use seat number (reliable) to look up the player, avoiding
    # regex issues with multi-word names.
    for m in RE_SUMMARY_SEAT.finditer(raw):
        seat_num = int(m.group("seat"))
        rest = m.group("rest") or ""
        if seat_num not in seats:
            continue
        seat_info = seats[seat_num]

        won_m = RE_SEAT_WON.search(rest)
        if won_m:
            amt = _dec(won_m.group("amount"))
            seat_info["won_amount"] = amt  # override with summary value

        if "showed" in rest:
            seat_info["showed_hand"] = True
            seat_info["went_to_showdown"] = True
        elif "mucked" in rest and "lost" in rest:
            seat_info["went_to_showdown"] = True

    # ── Summary pot info ─────────────────────────────────────────────────
    # Parse BEFORE net_won so we can reconcile uncalled bets.
    pot_m = RE_SUMMARY_POT.search(raw)
    if pot_m:
        hand["total_pot"] = _dec(pot_m.group("total"))
        hand["main_pot"] = _dec(pot_m.group("main"))
        stp_summary = _dec(pot_m.group("stp"))
        if stp_summary > 0:
            hand["stp_amount"] = stp_summary
        hand["rake"] = _dec(pot_m.group("rake"))
    else:
        hand["total_pot"] = Decimal("0")
        hand["main_pot"] = Decimal("0")
        hand["rake"] = Decimal("0")

    # ── Reconcile uncalled bets using pot total ──────────────────────────
    # BetRivers hand histories sometimes omit "Uncalled bet" return lines.
    # The Summary total pot equals the sum of money actually committed by
    # all players (excluding STP).  Any excess in our computed investments
    # is an uncalled bet that was silently returned to the last aggressor.
    _reconcile_uncalled(seats, actions, hand)

    # Net won = won_amount - total_invested
    for seat_info in seats.values():
        seat_info["net_won"] = (
            seat_info["won_amount"] - seat_info["total_invested"]
        )

    hand["raw_text"] = raw
    return hand


# ── File-level parsing ───────────────────────────────────────────────────────

# Hand histories in a file are separated by blank lines between blocks that
# each start with "BetRivers Poker Hand #".

RE_HAND_SPLIT = re.compile(r"\n(?=BetRivers Poker Hand #)")


def parse_file(filepath: str | Path) -> list[dict[str, Any]]:
    """Parse all hands from a single hand-history file and return a list of dicts."""
    text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    blocks = RE_HAND_SPLIT.split(text)
    hands = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        result = parse_hand(block)
        if result is not None:
            hands.append(result)
    return hands


def parse_directory(dirpath: str | Path) -> list[dict[str, Any]]:
    """Parse all .txt files in a directory."""
    dp = Path(dirpath)
    hands: list[dict[str, Any]] = []
    for f in sorted(dp.glob("*.txt")):
        hands.extend(parse_file(f))
    return hands


# ── Generator / streaming parsing ────────────────────────────────────────────

def parse_file_iter(filepath: str | Path):
    """Yield parsed hand dicts one at a time (avoids full materialization)."""
    content = Path(filepath).read_text(encoding="utf-8", errors="replace")
    for block in RE_HAND_SPLIT.split(content):
        block = block.strip()
        if not block:
            continue
        result = parse_hand(block)
        if result is not None:
            yield result


def parse_directory_iter(
    dirpath: str | Path,
    *,
    recursive: bool = False,
):
    """Yield parsed hands from all .txt files in a directory."""
    dp = Path(dirpath)
    glob_fn = dp.rglob if recursive else dp.glob
    for f in sorted(glob_fn("*.txt")):
        yield from parse_file_iter(f)


# ── Parallel parsing (multiprocessing) ───────────────────────────────────────

def parse_files_parallel(
    file_paths: list[str | Path],
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """
    Parse multiple files using a process pool for CPU parallelism.

    Each file is parsed in a separate process; results are combined
    in the same order as the input file list.
    """
    from concurrent.futures import ProcessPoolExecutor

    if not file_paths:
        return []

    # For very small batches, skip pool overhead
    if len(file_paths) <= 2:
        hands: list[dict[str, Any]] = []
        for f in file_paths:
            hands.extend(parse_file(f))
        return hands

    # Convert Path objects to strings for pickling reliability
    str_paths = [str(p) for p in file_paths]

    hands = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(parse_file, str_paths):
            hands.extend(result)
    return hands
