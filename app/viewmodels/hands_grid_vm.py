"""
View Model — Hands Grid Transformer.

Converts raw ``HandRow`` objects (from the data-access layer) into
display-ready ``HandGridRow`` values suitable for rendering in the
AG Grid / HTML table.

Responsibilities
────────────────
•  Compute derived columns: stack in BB, net-won in BB, all-in adj diff.
•  Build action-line summaries (street-by-street hero actions).
•  Build preflop-line labels (Open, 3-Bet, Cold Call, etc.).
•  Detect all-in street.
•  Format timestamps, stakes labels, etc.
•  Apply in-grid numeric filters (min/max BB won) that can't be pushed
   to SQL efficiently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from app.data_access.hands_repository import HandRow, HandFilter


# ── Suit symbols ──────────────────────────────────────────────────────────────

_SUIT_UNICODE = {"h": "♥", "d": "♦", "c": "♣", "s": "♠"}


def _card_to_unicode(card: str) -> str:
    """Convert a single card notation to Unicode: 'Kh' → 'K♥'."""
    if not card or len(card) < 2:
        return card
    rank = card[:-1]
    suit = card[-1].lower()
    return rank + _SUIT_UNICODE.get(suit, suit)


def _cards_to_unicode(cards_str: str) -> str:
    """Convert a space-separated card string to Unicode: '8d Ks' → '8♦ K♠'."""
    if not cards_str or not cards_str.strip():
        return ""
    return " ".join(_card_to_unicode(c) for c in cards_str.strip().split())


# ── Street / action constants ────────────────────────────────────────────────

STREET_ORDER = ["PREFLOP", "FLOP", "TURN", "RIVER"]

ACTION_ABBREV = {
    "fold": "F",
    "check": "X",
    "call": "C",
    "bet": "B",
    "raise": "R",
}


# ── Display row ──────────────────────────────────────────────────────────────

@dataclass
class HandGridRow:
    """One row in the hands-in-report grid — fully display-ready."""

    hand_id: int
    db_id: int
    time: str               # formatted timestamp
    stakes: str             # "$0.50/$1.00"
    stack_bb: float         # starting stack in big blinds
    cards: str              # raw card string for renderer ("8d Ks")
    position: str           # BTN, CO, MP, …
    line: str               # street-by-street hero actions ("R/CB/X")
    board: str              # raw board string ("Kh Ac 4s 2h As")
    net_won: float          # dollars
    net_won_bb: float       # big blinds
    allin_adj_diff: float   # EV diff (simplified)
    pf_line: str            # preflop description ("Open", "3-Bet", …)
    stp_amount: float       # Splash the Pot dead money added to pot


# ── Public API ───────────────────────────────────────────────────────────────

def transform_hands(
    rows: list[HandRow],
    filters: HandFilter | None = None,
) -> list[HandGridRow]:
    """
    Transform raw DB rows into display-ready grid rows.

    Also applies any in-grid numeric filters (min/max bb) that were
    deferred from the SQL layer.
    """
    result: list[HandGridRow] = []

    for row in rows:
        bb = float(row.big_blind) if row.big_blind else 1.0
        net = float(row.net_won)
        stack_bb = round(float(row.stack) / bb, 1) if bb > 0 else 0.0
        net_bb = round(net / bb, 2) if bb > 0 else 0.0

        line = _compute_action_line(row.hero_actions)
        pf_line = _compute_pf_line(row.hero_actions, row.all_preflop_actions, row.hp_id)
        allin_adj = _compute_allin_adj_diff(row)

        result.append(HandGridRow(
            hand_id=row.hand_id,
            db_id=row.db_id,
            time=row.played_at.strftime("%m/%d/%Y %H:%M") if row.played_at else "",
            stakes=f"${float(row.small_blind):.2f}/${float(row.big_blind):.2f}",
            stack_bb=stack_bb,
            cards=_cards_to_unicode(row.hole_cards or ""),
            position=row.position,
            line=line,
            board=_cards_to_unicode(row.board or ""),
            net_won=round(net, 2),
            net_won_bb=net_bb,
            allin_adj_diff=round(allin_adj, 2),
            pf_line=pf_line,
            stp_amount=round(float(row.stp_amount), 2),
        ))

    return result


def to_dataframe(grid_rows: list[HandGridRow]) -> pd.DataFrame:
    """
    Convert grid rows to a pandas DataFrame with display-friendly column names.

    The DataFrame is what gets passed to AG Grid / HTML rendering.
    """
    if not grid_rows:
        return pd.DataFrame()

    records = [
        {
            "_hand_id": r.hand_id,
            "_db_id": r.db_id,
            "Time": r.time,
            "Stakes": r.stakes,
            "Stack (bb)": r.stack_bb,
            "Cards": r.cards,
            "Position": r.position,
            "Line": r.line,
            "Board": r.board,
            "Net Won ($)": r.net_won,
            "Net Won (bb)": r.net_won_bb,
            "AI Adj Diff": r.allin_adj_diff,
            "PF Line": r.pf_line,
            "STP ($)": r.stp_amount,
        }
        for r in grid_rows
    ]
    return pd.DataFrame(records)


# ═════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═════════════════════════════════════════════════════════════════════════════

def _compute_action_line(hero_actions: list[dict]) -> str:
    """
    Build a street-by-street action abbreviation for the hero.

    Example output: ``R/CB/X/B``
    •  R = Raise, C = Call, B = Bet, X = Check, F = Fold, A = All-in
    •  Streets separated by ``/``; only streets the hero acted on appear.
    """
    if not hero_actions:
        return "-"

    by_street: dict[str, list[dict]] = {}
    for a in hero_actions:
        by_street.setdefault(a["street"], []).append(a)

    parts: list[str] = []
    for street in STREET_ORDER:
        acts = by_street.get(street)
        if not acts:
            continue
        acts_sorted = sorted(acts, key=lambda a: a["sequence"])
        abbrevs = ""
        for act in acts_sorted:
            if act.get("is_all_in"):
                abbrevs += "A"
            else:
                abbrevs += ACTION_ABBREV.get(act["action_type"], "?")
        parts.append(abbrevs)

    return "/".join(parts) if parts else "-"


def _compute_pf_line(
    hero_actions: list[dict],
    all_preflop: list[dict],
    hp_id: int,
) -> str:
    """
    Determine the preflop line description for the hero.

    Possible labels:
        Open, Limp, Cold Call, 3-Bet, 4-Bet, 5-Bet, Fold, Check (BB walk)
    """
    if not all_preflop:
        return "-"

    pf_hero = [a for a in hero_actions if a["street"] == "PREFLOP"]
    if not pf_hero:
        return "-"

    # Walk the global preflop action sequence until we find hero's first action
    raise_count_before = 0
    hero_first_action = None

    for a in all_preflop:
        if a["hand_player_id"] == hp_id:
            hero_first_action = a
            break
        if a["action_type"] == "raise":
            raise_count_before += 1

    if hero_first_action is None:
        return "-"

    action = hero_first_action["action_type"]

    if action == "fold":
        return "Fold"
    elif action == "check":
        return "Check"
    elif action == "call":
        if raise_count_before == 0:
            return "Limp"
        elif raise_count_before == 1:
            return "Cold Call"
        else:
            return "Call"
    elif action == "raise":
        if raise_count_before == 0:
            return "Open"
        elif raise_count_before == 1:
            return "3-Bet"
        elif raise_count_before == 2:
            return "4-Bet"
        else:
            return f"{raise_count_before + 1}-Bet"

    return "-"


def _compute_allin_street(hero_actions: list[dict]) -> str:
    """Return the street name where the hero first went all-in, or ``-``."""
    for a in hero_actions:
        if a.get("is_all_in"):
            street = a["street"]
            return street.capitalize() if street else "-"
    return "-"


def _compute_allin_adj_diff(row: HandRow) -> float:
    """
    Compute all-in adjusted EV difference using a simplified pot-equity model.

    This matches the calculation in ``stats.py``: when the hero is all-in and
    the hand goes to showdown, the EV share is ``(pot - rake) / SD_players``.
    The diff is ``EV_net - actual_net``.

    Returns 0.0 for hands where hero was not all-in at showdown.
    """
    hero_was_allin = any(a.get("is_all_in") for a in row.hero_actions)
    if not hero_was_allin or not row.went_to_showdown:
        return 0.0

    pot = float(row.total_pot)
    rake = float(row.rake)
    pot_after_rake = pot - rake
    sd_count = max(row.sd_player_count, 1)

    ev_won = pot_after_rake / sd_count
    ev_net = ev_won - float(row.total_invested)
    actual_net = float(row.net_won)

    return round(ev_net - actual_net, 2)
