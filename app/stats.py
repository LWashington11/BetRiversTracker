"""
Poker statistics — reads precomputed aggregates from player_stat_summaries.

The heavy per-hand computation is done at import time (see stat_flags.py +
importer.py).  This module simply reads the aggregated counters and computes
display percentages.  No action-table scans; no per-hand iteration.

Public API
──────────
get_hero_stats(hero_name, ...)   → (session_stats, cumulative, by_stakes, by_position)
get_filter_options(hero_name)    → (stakes_labels, game_type_labels)

For cross-dimensional filtering (date+stakes+position combined), we fall
back to aggregating from per-hand boolean flags stored on hand_players,
which is still fast — index scan on booleans, no join to actions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Integer as SAInteger, func, and_, or_, case
from sqlalchemy.orm import Session as SASession

from app.models import (
    Hand,
    Player,
    HandPlayer,
    PlayerStatSummary,
    PlayerCumulative,
    Session as SessionModel,
    SessionLocal,
)

# ── Position mapping (re-exported from constants for backward compat) ────────

from app.constants import (
    GAME_TYPE_SHORT as _GAME_TYPE_SHORT,
    POSITION_DISPLAY,
    POSITION_FILTER_MAP,
    POSITION_ORDER,
    ALL_POSITIONS,
    CUMULATIVE_MAX_POINTS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_hero(session: SASession, hero_name: str) -> Player | None:
    """Return the Player row for the hero, or None."""
    return session.query(Player).filter_by(name=hero_name).first()


def _pct(num: int, denom: int) -> float:
    """Safe percentage."""
    return round(num / denom * 100, 2) if denom else 0.0


def _to_stats(row) -> dict[str, Any]:
    """
    Convert a stat counter source into the display stat dict.

    Accepts either:
      - a PlayerStatSummary ORM row (attribute access), or
      - a dict from flag-aggregation (key access).
    """
    g = (lambda k: row.get(k) or 0) if isinstance(row, dict) else (lambda k: getattr(row, k, 0) or 0)

    total = g("total_hands")
    walks = g("walk_count")
    pfr_denom = total - walks

    postflop_bets = g("postflop_bets_raises")
    postflop_calls_ = g("postflop_calls")
    postflop_checks_ = g("postflop_checks")
    postflop_total = postflop_bets + postflop_calls_ + postflop_checks_
    agg_factor = (
        round(postflop_bets / postflop_calls_, 2)
        if postflop_calls_ > 0 else None
    )

    net = float(g("net_won"))
    ev_diff = float(g("allin_ev_diff"))

    return {
        "total_hands": total,
        "net_won": round(net, 2),
        "sd_won": round(float(g("sd_won")), 2),
        "nonsd_won": round(float(g("nonsd_won")), 2),
        "all_in_ev_diff": round(ev_diff, 2),
        "all_in_adj": round(net + ev_diff, 2),
        "bb_per_100": (
            round(float(g("bb_won_total")) / total * 100, 2) if total else 0.0
        ),
        "vpip": _pct(g("vpip_count"), pfr_denom),
        "pfr": _pct(g("pfr_count"), pfr_denom),
        "rfi": _pct(g("rfi_count"), g("rfi_opportunities")),
        "three_bet": _pct(g("three_bet_count"), g("three_bet_opportunities")),
        "four_bet": _pct(g("four_bet_count"), g("four_bet_opportunities")),
        "fold_to_3bet": _pct(
            g("fold_to_3bet_count"), g("fold_to_3bet_opportunities"),
        ),
        "wtsd_pct": _pct(g("went_to_sd_from_flop_count"), g("saw_flop_count")),
        "wssd_pct": _pct(g("won_at_sd_count"), g("went_to_sd_count")),
        "wwsf": _pct(g("won_when_saw_flop_count"), g("saw_flop_count")),
        "postflop_agg_pct": _pct(postflop_bets, postflop_total),
        "agg_factor": agg_factor,
        "flop_cbet": _pct(g("cbet_count"), g("cbet_opportunities")),
        "bet_turn_vs_missed_cbet": 0.0,
        "bet_river_vs_missed_cbet": 0.0,
        "fold_to_btn_steal": _pct(
            g("fold_to_btn_steal_count"), g("fold_to_btn_steal_opportunities"),
        ),
        "rake": round(float(g("rake_attributed")), 2),
        "rake_attributed": round(float(g("rake_from_won")), 2),
    }


def _empty_stats() -> dict[str, Any]:
    """Return a zeroed-out stat dict."""
    return {
        "session_start": None,
        "total_hands": 0,
        "net_won": 0.0,
        "sd_won": 0.0,
        "nonsd_won": 0.0,
        "all_in_ev_diff": 0.0,
        "all_in_adj": 0.0,
        "bb_per_100": 0.0,
        "vpip": 0.0,
        "pfr": 0.0,
        "rfi": 0.0,
        "three_bet": 0.0,
        "four_bet": 0.0,
        "fold_to_3bet": 0.0,
        "wtsd_pct": 0.0,
        "wssd_pct": 0.0,
        "wwsf": 0.0,
        "postflop_agg_pct": 0.0,
        "agg_factor": None,
        "flop_cbet": 0.0,
        "bet_turn_vs_missed_cbet": 0.0,
        "bet_river_vs_missed_cbet": 0.0,
        "fold_to_btn_steal": 0.0,
        "rake": 0.0,
        "rake_attributed": 0.0,
    }


# ── Fast path: read precomputed summary rows ────────────────────────────────

def _read_summaries(
    db: SASession,
    hero_id: int,
    grouping_type: str,
) -> list[PlayerStatSummary]:
    """Read all summary rows of a given grouping type for the hero."""
    return (
        db.query(PlayerStatSummary)
        .filter(
            PlayerStatSummary.player_id == hero_id,
            PlayerStatSummary.grouping_type == grouping_type,
        )
        .all()
    )


def _read_summaries_filtered(
    db: SASession,
    hero_id: int,
    grouping_type: str,
    group_keys: list[str] | None = None,
) -> list[PlayerStatSummary]:
    """Read summary rows optionally filtered to specific keys."""
    query = db.query(PlayerStatSummary).filter(
        PlayerStatSummary.player_id == hero_id,
        PlayerStatSummary.grouping_type == grouping_type,
    )
    if group_keys:
        query = query.filter(PlayerStatSummary.group_key.in_(group_keys))
    return query.all()


# ── Slow path: aggregate from hand_player flags (for cross-dim filters) ─────

def _aggregate_from_flags(
    db: SASession,
    hero_id: int,
    date_from: date | None,
    date_to: date | None,
    stakes_filter: list[str] | None,
    game_type_filter: list[str] | None,
    position_filter: list[str] | None,
    group_by: str,
) -> list[dict[str, Any]]:
    """
    Aggregate stats from hand_players boolean flags when precomputed
    summaries can't be used (multiple filter dimensions active).

    group_by: 'date', 'stakes', 'position'
    """
    # Build the grouping expression
    if group_by == "date":
        group_expr = Hand.played_date
    elif group_by == "stakes":
        # Produce the same key format as _stakes_game_key():
        # "$0.50/$1.00 NL Holdem|6 Max"
        game_abbr = case(
            (Hand.game_type == "Hold'em No Limit", "NL Holdem"),
            (Hand.game_type == "Omaha Pot Limit", "PL Omaha"),
            (Hand.game_type == "Hold'em Pot Limit", "PL Holdem"),
            (Hand.game_type == "Omaha No Limit", "NL Omaha"),
            else_=Hand.game_type,
        )
        seats_label = case(
            (Hand.max_seats <= 2, "HU"),
            (Hand.max_seats <= 6, "6 Max"),
            (Hand.max_seats <= 9, "9 Max"),
            else_=func.concat(Hand.max_seats, " Max"),
        )
        group_expr = func.concat(
            "$", Hand.small_blind, "/$", Hand.big_blind,
            " ", game_abbr, "|", seats_label,
        )
    elif group_by == "position":
        group_expr = HandPlayer.position
    else:
        group_expr = func.literal("all")

    query = (
        db.query(
            group_expr.label("group_key"),
            func.count(HandPlayer.id).label("total_hands"),
            func.sum(func.cast(HandPlayer.was_walk, SAInteger)).label("walk_count"),
            func.sum(func.cast(HandPlayer.was_vpip, SAInteger)).label("vpip_count"),
            func.sum(func.cast(HandPlayer.was_pfr, SAInteger)).label("pfr_count"),
            func.sum(func.cast(HandPlayer.was_rfi, SAInteger)).label("rfi_count"),
            func.sum(func.cast(HandPlayer.had_rfi_opp, SAInteger)).label("rfi_opportunities"),
            func.sum(func.cast(HandPlayer.was_3bet, SAInteger)).label("three_bet_count"),
            func.sum(func.cast(HandPlayer.had_3bet_opp, SAInteger)).label("three_bet_opportunities"),
            func.sum(func.cast(HandPlayer.was_4bet, SAInteger)).label("four_bet_count"),
            func.sum(func.cast(HandPlayer.had_4bet_opp, SAInteger)).label("four_bet_opportunities"),
            func.sum(func.cast(HandPlayer.folded_to_3bet, SAInteger)).label("fold_to_3bet_count"),
            func.sum(func.cast(
                and_(HandPlayer.faced_3bet == True, HandPlayer.had_rfi_opp == True, HandPlayer.was_rfi == True),
                SAInteger,
            )).label("fold_to_3bet_opportunities"),
            func.sum(func.cast(HandPlayer.saw_flop, SAInteger)).label("saw_flop_count"),
            func.sum(func.cast(HandPlayer.went_to_showdown, SAInteger)).label("went_to_sd_count"),
            func.sum(func.cast(
                and_(HandPlayer.saw_flop == True, HandPlayer.went_to_showdown == True),
                SAInteger,
            )).label("went_to_sd_from_flop_count"),
            func.sum(func.cast(
                and_(HandPlayer.went_to_showdown == True, HandPlayer.net_won > 0),
                SAInteger,
            )).label("won_at_sd_count"),
            func.sum(func.cast(
                and_(HandPlayer.saw_flop == True, HandPlayer.net_won > 0),
                SAInteger,
            )).label("won_when_saw_flop_count"),
            func.sum(func.cast(HandPlayer.was_cbet, SAInteger)).label("cbet_count"),
            func.sum(func.cast(HandPlayer.had_cbet_opp, SAInteger)).label("cbet_opportunities"),
            func.sum(func.cast(HandPlayer.folded_to_btn_steal, SAInteger)).label("fold_to_btn_steal_count"),
            func.sum(func.cast(HandPlayer.faced_btn_steal, SAInteger)).label("fold_to_btn_steal_opportunities"),
            func.sum(HandPlayer.postflop_bets_raises).label("postflop_bets_raises"),
            func.sum(HandPlayer.postflop_calls).label("postflop_calls"),
            func.sum(HandPlayer.postflop_checks).label("postflop_checks"),
            func.sum(HandPlayer.net_won).label("net_won"),
            func.sum(
                case(
                    (HandPlayer.went_to_showdown == True, HandPlayer.net_won),
                    else_=Decimal("0"),
                )
            ).label("sd_won"),
            func.sum(
                case(
                    (HandPlayer.went_to_showdown == False, HandPlayer.net_won),
                    else_=Decimal("0"),
                )
            ).label("nonsd_won"),
            func.sum(HandPlayer.bb_won).label("bb_won_total"),
            func.sum(HandPlayer.allin_ev_diff).label("allin_ev_diff"),
            func.sum(HandPlayer.rake_from_won).label("rake_from_won"),
            func.sum(HandPlayer.rake_attributed).label("rake_attributed"),
        )
        .join(Hand, Hand.id == HandPlayer.hand_id)
        .filter(
            HandPlayer.player_id == hero_id,
            HandPlayer.is_sitting_out == False,
        )
    )

    # Apply filters
    if date_from:
        query = query.filter(Hand.played_date >= date_from)
    if date_to:
        query = query.filter(Hand.played_date <= date_to)
    if game_type_filter:
        query = query.filter(Hand.game_type.in_(game_type_filter))
    if stakes_filter:
        conditions = []
        for s in stakes_filter:
            parts = s.replace("$", "").split("/")
            if len(parts) == 2:
                try:
                    sb_val, bb_val = Decimal(parts[0]), Decimal(parts[1])
                    conditions.append(
                        and_(Hand.small_blind == sb_val, Hand.big_blind == bb_val)
                    )
                except Exception:
                    pass
        if conditions:
            query = query.filter(or_(*conditions))
    if position_filter:
        stored = []
        for p in position_filter:
            stored.extend(POSITION_FILTER_MAP.get(p, [p]))
        query = query.filter(HandPlayer.position.in_(stored))

    query = query.group_by(group_expr)
    return [dict(row._mapping) for row in query.all()]



# ── Determine whether we can use the fast path ──────────────────────────────

def _needs_cross_filter(
    date_from: date | None,
    date_to: date | None,
    stakes_filter: list[str] | None,
    game_type_filter: list[str] | None,
    position_filter: list[str] | None,
) -> bool:
    """
    Return True when any filter is active so all tables and the chart
    are scoped to exactly the matching hands.

    Even a single-dimension filter (e.g. position only) must aggregate
    from hand_player flags so that session stats, the cumulative chart,
    and every breakdown table all respect the same filter.
    """
    return bool(
        date_from or date_to
        or stakes_filter
        or game_type_filter
        or position_filter
    )


# ── Session-start time lookup ────────────────────────────────────────────────

def _session_starts(
    db: SASession,
    hero_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[date, Any]:
    """Return {played_date: earliest played_at} for each session day."""
    query = (
        db.query(
            Hand.played_date,
            func.min(Hand.played_at).label("start"),
        )
        .join(HandPlayer, HandPlayer.hand_id == Hand.id)
        .filter(
            HandPlayer.player_id == hero_id,
            HandPlayer.is_sitting_out == False,
        )
        .group_by(Hand.played_date)
    )
    if date_from:
        query = query.filter(Hand.played_date >= date_from)
    if date_to:
        query = query.filter(Hand.played_date <= date_to)
    return {row[0]: row[1] for row in query.all()}


# ── Cumulative P&L from precomputed table ────────────────────────────────────

# Maximum data points returned for the cumulative chart.  Beyond this
# threshold the query downsamples by taking every N-th row, always
# including the first and last rows for accuracy.
_MAX_CUMULATIVE_POINTS = CUMULATIVE_MAX_POINTS


def _read_cumulative(
    db: SASession,
    hero_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Read cumulative P&L data from the player_cumulative table."""
    query = (
        db.query(PlayerCumulative)
        .filter(PlayerCumulative.player_id == hero_id)
    )
    if date_from:
        query = query.filter(PlayerCumulative.played_at >= date_from)
    if date_to:
        # Include to end of day
        from datetime import datetime, timedelta
        end = datetime.combine(date_to, datetime.max.time())
        query = query.filter(PlayerCumulative.played_at <= end)

    # Check total count to decide whether downsampling is needed
    total = query.with_entities(func.count()).order_by(None).scalar()
    if total > _MAX_CUMULATIVE_POINTS:
        # Downsample using PK modulo — O(N/step) without a full
        # ROW_NUMBER() window-function scan.
        step = total // _MAX_CUMULATIVE_POINTS

        # Fetch the boundary IDs so we always include first and last.
        bounds = query.with_entities(
            func.min(PlayerCumulative.id),
            func.max(PlayerCumulative.id),
        ).one()
        min_id, max_id = bounds

        query = query.filter(
            or_(
                PlayerCumulative.id % step == 0,
                PlayerCumulative.id == min_id,
                PlayerCumulative.id == max_id,
            )
        )

    query = query.order_by(PlayerCumulative.hand_number)
    rows = query.all()

    # Renumber hands when date-filtered or downsampled
    results = []
    for i, r in enumerate(rows, 1):
        results.append({
            "hand_num": i,
            "hand_id": r.hand_id,
            "played_at": r.played_at,
            "net_won_cumulative": float(r.net_won_cumulative or 0),
            "sd_won_cumulative": float(r.sd_won_cumulative or 0),
            "nonsd_won_cumulative": float(r.nonsd_won_cumulative or 0),
            "allin_ev_cumulative": float(r.allin_ev_cumulative or 0),
        })
    return results


def _read_cumulative_from_flags(
    db: SASession,
    hero_id: int,
    date_from: date | None,
    date_to: date | None,
    stakes_filter: list[str] | None,
    game_type_filter: list[str] | None,
    position_filter: list[str] | None,
) -> list[dict[str, Any]]:
    """
    Compute cumulative P&L on the fly from hand_player rows so that
    stakes, game-type, and position filters are all respected.
    """
    query = (
        db.query(
            HandPlayer.hand_id,
            Hand.played_at,
            HandPlayer.net_won,
            HandPlayer.went_to_showdown,
            HandPlayer.allin_ev_diff,
        )
        .join(Hand, Hand.id == HandPlayer.hand_id)
        .filter(
            HandPlayer.player_id == hero_id,
            HandPlayer.is_sitting_out == False,
        )
    )

    if date_from:
        query = query.filter(Hand.played_date >= date_from)
    if date_to:
        query = query.filter(Hand.played_date <= date_to)
    if game_type_filter:
        query = query.filter(Hand.game_type.in_(game_type_filter))
    if stakes_filter:
        conditions = []
        for s in stakes_filter:
            parts = s.replace("$", "").split("/")
            if len(parts) == 2:
                try:
                    sb_val, bb_val = Decimal(parts[0]), Decimal(parts[1])
                    conditions.append(
                        and_(Hand.small_blind == sb_val, Hand.big_blind == bb_val)
                    )
                except Exception:
                    pass
        if conditions:
            query = query.filter(or_(*conditions))
    if position_filter:
        stored = []
        for p in position_filter:
            stored.extend(POSITION_FILTER_MAP.get(p, [p]))
        query = query.filter(HandPlayer.position.in_(stored))

    query = query.order_by(Hand.played_at, HandPlayer.hand_id)
    rows = query.all()

    total = len(rows)
    if total == 0:
        return []

    # Decide which indices to emit (same downsampling logic as _read_cumulative)
    if total > _MAX_CUMULATIVE_POINTS:
        step = total // _MAX_CUMULATIVE_POINTS
        emit = set(range(0, total, step)) | {0, total - 1}
    else:
        emit = None

    cum_net = Decimal("0")
    cum_sd = Decimal("0")
    cum_nonsd = Decimal("0")
    cum_ev = Decimal("0")
    results = []
    for i, r in enumerate(rows):
        net = r.net_won or Decimal("0")
        cum_net += net
        cum_ev += r.allin_ev_diff or Decimal("0")
        if r.went_to_showdown:
            cum_sd += net
        else:
            cum_nonsd += net
        if emit is None or i in emit:
            results.append({
                "hand_num": i + 1,
                "hand_id": r.hand_id,
                "played_at": r.played_at,
                "net_won_cumulative": float(cum_net),
                "sd_won_cumulative": float(cum_sd),
                "nonsd_won_cumulative": float(cum_nonsd),
                "allin_ev_cumulative": float(cum_net + cum_ev),
            })
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def get_hero_stats(
    hero_name: str,
    date_from=None,
    date_to=None,
    stakes_filter=None,
    game_type_filter=None,
    position_filter=None,
):
    """
    Public API: return (session_stats, cumulative, by_stakes, by_position)
    for the given hero.

    Uses precomputed aggregation tables when possible (fast path), or
    aggregates from per-hand boolean flags when cross-dimensional filters
    are active (slow path — still fast, just more SQL work).
    """
    db = SessionLocal()
    try:
        hero = _ensure_hero(db, hero_name)
        if hero is None:
            return [], [], [], []

        cross = _needs_cross_filter(
            date_from, date_to, stakes_filter, game_type_filter, position_filter
        )

        if cross:
            return _get_stats_slow_path(
                db, hero.id,
                date_from, date_to, stakes_filter, game_type_filter,
                position_filter,
            )
        else:
            return _get_stats_fast_path(
                db, hero.id,
                date_from, date_to, stakes_filter, game_type_filter,
                position_filter,
            )
    finally:
        db.close()


def _get_stats_fast_path(
    db: SASession,
    hero_id: int,
    date_from: date | None,
    date_to: date | None,
    stakes_filter: list[str] | None,
    game_type_filter: list[str] | None,
    position_filter: list[str] | None,
) -> tuple:
    """Read directly from precomputed summary rows."""
    session_starts = _session_starts(db, hero_id, date_from, date_to)

    # ── Session stats (by date) ──────────────────────────────────────
    date_rows = _read_summaries(db, hero_id, "date")
    session_stats = []
    for row in date_rows:
        d = _parse_date_key(row.group_key)
        if d is None:
            continue
        # Apply date range filter
        if date_from and d < date_from:
            continue
        if date_to and d > date_to:
            continue
        stats = _to_stats(row)
        stats["hand_date"] = d
        stats["session_start"] = session_starts.get(d)
        session_stats.append(stats)
    session_stats.sort(key=lambda s: s["hand_date"])

    # ── By stakes ────────────────────────────────────────────────────
    stakes_rows = _read_summaries(db, hero_id, "stakes")
    by_stakes = []
    for row in stakes_rows:
        # Apply stakes filter
        if stakes_filter:
            # group_key looks like "$0.50/$1.00 NL Holdem|6 Max"
            stakes_part = row.group_key.split("|")[0].split()[0]  # "$0.50/$1.00"
            if stakes_part not in stakes_filter:
                continue
        # Apply game type filter
        if game_type_filter:
            key_game = row.group_key.split("|")[0]  # "$0.50/$1.00 NL Holdem"
            match = any(
                _GAME_TYPE_SHORT.get(gt, gt) in key_game
                for gt in game_type_filter
            )
            if not match:
                continue
        stats = _to_stats(row)
        parts = row.group_key.split("|")
        stats["stakes"] = parts[0] if parts else row.group_key
        stats["seats"] = parts[1] if len(parts) > 1 else "?"
        by_stakes.append(stats)
    by_stakes.sort(key=lambda s: (
        float(s["stakes"].split("/")[1].split()[0].replace("$", ""))
        if "/" in s["stakes"] else 0
    ))

    # ── By position ──────────────────────────────────────────────────
    pos_rows = _read_summaries(db, hero_id, "position")
    by_position = []
    for row in pos_rows:
        if position_filter:
            if row.group_key not in position_filter:
                continue
        stats = _to_stats(row)
        stats["position"] = row.group_key
        by_position.append(stats)
    by_position.sort(key=lambda s: POSITION_ORDER.get(s["position"], 99))

    # ── Cumulative P&L ───────────────────────────────────────────────
    cumulative = _read_cumulative(db, hero_id, date_from, date_to)

    return session_stats, cumulative, by_stakes, by_position


def _get_stats_slow_path(
    db: SASession,
    hero_id: int,
    date_from: date | None,
    date_to: date | None,
    stakes_filter: list[str] | None,
    game_type_filter: list[str] | None,
    position_filter: list[str] | None,
) -> tuple:
    """Aggregate from hand_player boolean flags (for cross-dimensional filters)."""
    session_starts = _session_starts(db, hero_id, date_from, date_to)

    # ── Session stats (by date) ──────────────────────────────────────
    date_aggs = _aggregate_from_flags(
        db, hero_id, date_from, date_to, stakes_filter, game_type_filter,
        position_filter, group_by="date",
    )
    session_stats = []
    for row in date_aggs:
        d = row["group_key"]
        stats = _to_stats(row)
        stats["hand_date"] = d
        stats["session_start"] = session_starts.get(d)
        session_stats.append(stats)
    session_stats.sort(key=lambda s: s["hand_date"])

    # ── By stakes ────────────────────────────────────────────────────
    stakes_aggs = _aggregate_from_flags(
        db, hero_id, date_from, date_to, stakes_filter, game_type_filter,
        position_filter, group_by="stakes",
    )
    by_stakes = []
    for row in stakes_aggs:
        stats = _to_stats(row)
        key = row["group_key"]
        parts = key.split("|") if "|" in key else [key, "?"]
        stats["stakes"] = parts[0]
        stats["seats"] = parts[1] if len(parts) > 1 else "?"
        by_stakes.append(stats)

    # ── By position ──────────────────────────────────────────────────
    pos_aggs = _aggregate_from_flags(
        db, hero_id, date_from, date_to, stakes_filter, game_type_filter,
        position_filter, group_by="position",
    )
    by_position = []
    for row in pos_aggs:
        stats = _to_stats(row)
        pos = row["group_key"] or "?"
        stats["position"] = POSITION_DISPLAY.get(pos, pos)
        by_position.append(stats)
    by_position.sort(key=lambda s: POSITION_ORDER.get(s["position"], 99))

    # ── Cumulative P&L ───────────────────────────────────────────────
    cumulative = _read_cumulative_from_flags(
        db, hero_id, date_from, date_to,
        stakes_filter, game_type_filter, position_filter,
    )

    return session_stats, cumulative, by_stakes, by_position


def _parse_date_key(key: str) -> date | None:
    """Parse a group_key like '2025-09-29' into a date object."""
    try:
        parts = key.split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


# ── Filter option discovery ──────────────────────────────────────────────────

def get_filter_options(hero_name: str):
    """
    Return (stakes_labels, game_type_labels) available in the DB for the hero.
    """
    db = SessionLocal()
    try:
        hero = _ensure_hero(db, hero_name)
        if not hero:
            return [], []

        stakes_rows = (
            db.query(Hand.small_blind, Hand.big_blind)
            .join(HandPlayer, HandPlayer.hand_id == Hand.id)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,
            )
            .distinct()
            .all()
        )
        stakes_labels = sorted(
            [f"${float(sb):.2f}/${float(bb):.2f}" for sb, bb in stakes_rows],
            key=lambda s: float(s.split("/")[1].replace("$", "")),
        )

        gt_rows = (
            db.query(Hand.game_type)
            .join(HandPlayer, HandPlayer.hand_id == Hand.id)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,
            )
            .distinct()
            .all()
        )
        game_type_labels = sorted([gt[0] for gt in gt_rows])

        return stakes_labels, game_type_labels
    finally:
        db.close()
