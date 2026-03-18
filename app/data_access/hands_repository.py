"""
Data Access Layer — Hands Repository.

Provides filtered, paginated, server-side sorted hand retrieval
for the hero player.  All queries join on pre-existing indexes and
batch-fetch related data (actions, showdown counts) in bulk to
avoid N+1 problems.

Performance strategy
────────────────────
•  The main query filters on (player_id, is_sitting_out) using the
   ``ix_hp_player_active`` index, then joins ``hands`` on the PK.
•  Stake/position/date filters apply additional WHERE clauses that
   leverage ``ix_hands_blinds``, ``ix_hp_player_position``, and
   ``idx_hands_played_date``.
•  Actions are batch-fetched in two bulk queries (hero-only + all
   preflop) keyed by hand_player_id / hand_id.
•  Showdown player counts are fetched in one GROUP BY query.

All public functions open and close their own DB session.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, and_, or_, desc, asc
from sqlalchemy.orm import Session as SASession

from app.models import (
    Hand,
    Player,
    HandPlayer,
    Action,
    Session as SessionModel,
    SessionLocal,
)


# ── Filter & sort specs ──────────────────────────────────────────────────────

@dataclass
class HandFilter:
    """Criteria for narrowing the hands-in-report result set."""

    session_date: date | None = None             # single session day
    session_dates: list[date] | None = None      # multiple session days
    positions: list[str] | None = None           # e.g. ["BB", "SB"]
    stakes: list[str] | None = None              # e.g. ["$0.50/$1.00"]
    date_from: date | None = None
    date_to: date | None = None
    game_type: str | None = None
    # In-grid filters (applied post-fetch in the view model)
    min_net_won_bb: float | None = None
    max_net_won_bb: float | None = None


from app.constants import POSITION_FILTER_MAP


@dataclass
class SortSpec:
    """Column + direction for server-side ordering."""

    column: str = "played_at"
    direction: Literal["asc", "desc"] = "desc"


# ── Row dataclass ────────────────────────────────────────────────────────────

@dataclass
class HandRow:
    """Raw hand data for one hero participation — directly from the DB."""

    hand_id: int                # original site hand_id
    db_id: int                  # hands.id (internal PK)
    hp_id: int                  # hand_players.id — links to actions
    played_at: datetime
    small_blind: Decimal
    big_blind: Decimal
    stack: Decimal
    hole_cards: str | None
    board: str | None
    position: str
    net_won: Decimal
    total_invested: Decimal
    won_amount: Decimal
    went_to_showdown: bool
    total_pot: Decimal
    rake: Decimal
    stp_amount: Decimal
    max_seats: int
    game_type: str
    # Populated after the main query via batch helpers
    hero_actions: list[dict] = field(default_factory=list)
    all_preflop_actions: list[dict] = field(default_factory=list)
    sd_player_count: int = 1


# ── Sortable column mapping ─────────────────────────────────────────────────

_SORT_COLUMNS = {
    "played_at": Hand.played_at,
    "hand_id": Hand.hand_id,
    "stakes": Hand.big_blind,
    "stack_bb": HandPlayer.stack,
    "net_won": HandPlayer.net_won,
    "position": HandPlayer.position,
}


# ═════════════════════════════════════════════════════════════════════════════
# Public API
# ═════════════════════════════════════════════════════════════════════════════

def fetch_hands_for_report(
    hero_name: str,
    filters: HandFilter,
    sort: SortSpec | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> tuple[list[HandRow], int]:
    """
    Fetch hands matching *filters* for the report grid.

    Returns
    -------
    (rows, total_count)
        *rows* — data for the current page/window.
        *total_count* — total matching hands (for pagination display).
    """
    db = SessionLocal()
    try:
        hero = db.query(Player).filter_by(name=hero_name).first()
        if not hero:
            return [], 0

        query = (
            db.query(HandPlayer, Hand)
            .join(Hand, Hand.id == HandPlayer.hand_id)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,  # noqa: E712
            )
        )
        query = _apply_filters(query, filters, db)

        # Use a count subquery so we don't execute the full filter twice
        from sqlalchemy import func as sa_func
        count_query = query.with_entities(sa_func.count()).order_by(None)
        total_count = count_query.scalar()

        # Sorting
        if sort:
            col_expr = _SORT_COLUMNS.get(sort.column, Hand.played_at)
            order_fn = desc if sort.direction == "desc" else asc
            query = query.order_by(order_fn(col_expr))
        else:
            query = query.order_by(desc(Hand.played_at))

        query = query.offset(offset).limit(limit)
        results = query.all()

        if not results:
            return [], total_count

        rows: list[HandRow] = []
        for hp, h in results:
            rows.append(HandRow(
                hand_id=h.hand_id,
                db_id=h.id,
                hp_id=hp.id,
                played_at=h.played_at,
                small_blind=h.small_blind or Decimal("0"),
                big_blind=h.big_blind or Decimal("0"),
                stack=hp.stack or Decimal("0"),
                hole_cards=hp.hole_cards,
                board=h.board,
                position=hp.position or "?",
                net_won=hp.net_won or Decimal("0"),
                total_invested=hp.total_invested or Decimal("0"),
                won_amount=hp.won_amount or Decimal("0"),
                went_to_showdown=hp.went_to_showdown or False,
                total_pot=h.total_pot or Decimal("0"),
                rake=h.rake or Decimal("0"),
                stp_amount=h.stp_amount or Decimal("0"),
                max_seats=h.max_seats or 6,
                game_type=h.game_type or "Hold'em No Limit",
            ))

        # Batch-attach actions & showdown counts
        _attach_actions(db, rows)
        _attach_showdown_counts(db, rows)

        return rows, total_count
    finally:
        db.close()


def fetch_filter_options(
    hero_name: str,
) -> tuple[list[dict], list[str], list[str]]:
    """
    Return (session_dates, stakes_labels, positions) in a single DB session.

    Avoids opening three separate connections for the filter sidebar.
    """
    db = SessionLocal()
    try:
        hero = db.query(Player).filter_by(name=hero_name).first()
        if not hero:
            return [], [], []

        # 1) Session dates
        date_rows = (
            db.query(
                Hand.played_date,
                func.count(Hand.id).label("cnt"),
                func.sum(HandPlayer.net_won).label("net"),
            )
            .join(HandPlayer, HandPlayer.hand_id == Hand.id)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,  # noqa: E712
            )
            .group_by(Hand.played_date)
            .order_by(desc(Hand.played_date))
            .all()
        )
        session_dates = []
        for played_date, cnt, net in date_rows:
            net_f = float(net or 0)
            sign = "+" if net_f >= 0 else ""
            session_dates.append({
                "date": played_date,
                "total_hands": cnt,
                "net_won": net_f,
                "label": (
                    f"{played_date}  |  {cnt:,} hands  |  "
                    f"{sign}${net_f:,.2f}"
                ),
            })

        # 2) Distinct stakes
        stakes_rows = (
            db.query(Hand.small_blind, Hand.big_blind)
            .join(HandPlayer, HandPlayer.hand_id == Hand.id)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,  # noqa: E712
            )
            .distinct()
            .all()
        )
        stakes = sorted(
            [f"${float(sb):.2f}/${float(bb):.2f}" for sb, bb in stakes_rows],
            key=lambda s: float(s.split("/")[1].replace("$", "")),
        )

        # 3) Distinct positions
        pos_rows = (
            db.query(HandPlayer.position)
            .filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,  # noqa: E712
                HandPlayer.position.isnot(None),
            )
            .distinct()
            .all()
        )
        order = {"BTN": 0, "CO": 1, "MP": 2, "UTG": 3, "EP": 3, "SB": 4, "BB": 5}
        positions = sorted(
            [r[0] for r in pos_rows if r[0]],
            key=lambda p: order.get(p, 99),
        )

        return session_dates, stakes, positions
    finally:
        db.close()


def fetch_session_dates(hero_name: str) -> list[dict]:
    """Return distinct session dates for the hero, most recent first."""
    dates, _, _ = fetch_filter_options(hero_name)
    return dates


def fetch_available_stakes(hero_name: str) -> list[str]:
    """Distinct stakes labels the hero has played."""
    _, stakes, _ = fetch_filter_options(hero_name)
    return stakes


def fetch_available_positions(hero_name: str) -> list[str]:
    """Distinct positions the hero has been dealt in."""
    _, _, positions = fetch_filter_options(hero_name)
    return positions


# ═════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═════════════════════════════════════════════════════════════════════════════

def _apply_filters(query, filters: HandFilter, db: SASession):
    """Translate HandFilter fields into SQLAlchemy WHERE clauses."""

    if filters.session_date is not None:
        query = query.filter(Hand.played_date == filters.session_date)

    if filters.session_dates:
        query = query.filter(Hand.played_date.in_(filters.session_dates))

    if filters.positions:
        stored: list[str] = []
        for p in filters.positions:
            stored.extend(POSITION_FILTER_MAP.get(p, [p]))
        query = query.filter(HandPlayer.position.in_(stored))

    if filters.stakes:
        conditions = []
        for s in filters.stakes:
            parts = s.replace("$", "").split("/")
            if len(parts) == 2:
                try:
                    sb_val = Decimal(parts[0])
                    bb_val = Decimal(parts[1])
                    conditions.append(
                        and_(Hand.small_blind == sb_val, Hand.big_blind == bb_val)
                    )
                except (ValueError, ArithmeticError):
                    pass
        if conditions:
            query = query.filter(or_(*conditions))

    if filters.date_from:
        query = query.filter(Hand.played_date >= filters.date_from)
    if filters.date_to:
        query = query.filter(Hand.played_date <= filters.date_to)

    if filters.game_type:
        query = query.filter(Hand.game_type == filters.game_type)

    # Push net-won-in-bb filters to SQL (uses bb_won column on hand_players)
    if filters.min_net_won_bb is not None:
        query = query.filter(HandPlayer.bb_won >= Decimal(str(filters.min_net_won_bb)))
    if filters.max_net_won_bb is not None:
        query = query.filter(HandPlayer.bb_won <= Decimal(str(filters.max_net_won_bb)))

    return query


def _attach_actions(db: SASession, rows: list[HandRow]) -> None:
    """
    Batch-fetch hero actions + all preflop actions and attach to rows.

    Queries are chunked to avoid massive IN clauses when row count
    is large (e.g. 10K rows → 10K IDs in a single IN is slow on PG).
    """
    if not rows:
        return

    _IN_CHUNK = 1000  # max IDs per IN clause

    hp_ids = [r.hp_id for r in rows]
    hand_ids = [r.db_id for r in rows]

    # 1) Hero's own actions, keyed by hand_player_id
    by_hp: dict[int, list[dict]] = {}
    for i in range(0, len(hp_ids), _IN_CHUNK):
        chunk = hp_ids[i : i + _IN_CHUNK]
        hero_actions = (
            db.query(Action)
            .filter(Action.hand_player_id.in_(chunk))
            .order_by(Action.hand_id, Action.street, Action.sequence)
            .all()
        )
        for a in hero_actions:
            by_hp.setdefault(a.hand_player_id, []).append({
                "street": a.street,
                "sequence": a.sequence,
                "action_type": a.action_type,
                "amount": float(a.amount or 0),
                "raise_to": float(a.raise_to) if a.raise_to else None,
                "is_all_in": a.is_all_in or False,
            })

    # 2) ALL preflop actions (needed for PF-line context: raise counts)
    by_hand: dict[int, list[dict]] = {}
    for i in range(0, len(hand_ids), _IN_CHUNK):
        chunk = hand_ids[i : i + _IN_CHUNK]
        all_preflop = (
            db.query(Action)
            .filter(
                Action.hand_id.in_(chunk),
                Action.street == "PREFLOP",
            )
            .order_by(Action.hand_id, Action.sequence)
            .all()
        )
        for a in all_preflop:
            by_hand.setdefault(a.hand_id, []).append({
                "hand_player_id": a.hand_player_id,
                "sequence": a.sequence,
                "action_type": a.action_type,
                "amount": float(a.amount or 0),
                "is_all_in": a.is_all_in or False,
            })

    for row in rows:
        row.hero_actions = by_hp.get(row.hp_id, [])
        row.all_preflop_actions = by_hand.get(row.db_id, [])


def _attach_showdown_counts(db: SASession, rows: list[HandRow]) -> None:
    """Batch-fetch the number of players that went to showdown per hand."""
    if not rows:
        return

    _IN_CHUNK = 1000
    hand_ids = [r.db_id for r in rows]
    sd_map: dict[int, int] = {}
    for i in range(0, len(hand_ids), _IN_CHUNK):
        chunk = hand_ids[i : i + _IN_CHUNK]
        sd_rows = (
            db.query(
                HandPlayer.hand_id,
                func.count(HandPlayer.id),
            )
            .filter(
                HandPlayer.hand_id.in_(chunk),
                HandPlayer.went_to_showdown == True,  # noqa: E712
            )
            .group_by(HandPlayer.hand_id)
            .all()
        )
        for hid, cnt in sd_rows:
            sd_map[hid] = cnt
    for row in rows:
        row.sd_player_count = sd_map.get(row.db_id, 1)


# ═════════════════════════════════════════════════════════════════════════════
# Purge hands
# ═════════════════════════════════════════════════════════════════════════════

_DELETE_CHUNK = 1000  # hand IDs per DELETE batch

# Column names that are subtracted from player_stat_summaries during
# delta-based purge.  Order must match the SELECT in _compute_deltas().
_DELTA_COLS = (
    "total_hands", "walk_count", "vpip_count", "pfr_count",
    "rfi_count", "rfi_opportunities",
    "three_bet_count", "three_bet_opportunities",
    "four_bet_count", "four_bet_opportunities",
    "fold_to_3bet_count", "fold_to_3bet_opportunities",
    "saw_flop_count", "went_to_sd_count", "went_to_sd_from_flop_count",
    "won_at_sd_count", "won_when_saw_flop_count",
    "cbet_count", "cbet_opportunities",
    "fold_to_btn_steal_count", "fold_to_btn_steal_opportunities",
    "postflop_bets_raises", "postflop_calls", "postflop_checks",
    "net_won", "sd_won", "nonsd_won", "bb_won_total",
    "allin_ev_diff", "rake_from_won", "rake_attributed",
)

# SQL aggregation fragment matching _DELTA_COLS order.
_DELTA_AGG = """
    COUNT(*),
    SUM(CASE WHEN hp.was_walk THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_vpip THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_pfr THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_rfi THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.had_rfi_opp THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_3bet THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.had_3bet_opp THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_4bet THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.had_4bet_opp THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.folded_to_3bet THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.faced_3bet AND hp.had_rfi_opp AND hp.was_rfi
              THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.saw_flop THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.went_to_showdown THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.went_to_showdown AND hp.saw_flop
              THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.went_to_showdown AND hp.net_won > 0
              THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.saw_flop AND hp.net_won > 0
              THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.was_cbet THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.had_cbet_opp THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.folded_to_btn_steal THEN 1 ELSE 0 END),
    SUM(CASE WHEN hp.faced_btn_steal THEN 1 ELSE 0 END),
    COALESCE(SUM(hp.postflop_bets_raises), 0),
    COALESCE(SUM(hp.postflop_calls), 0),
    COALESCE(SUM(hp.postflop_checks), 0),
    COALESCE(SUM(hp.net_won), 0),
    COALESCE(SUM(CASE WHEN hp.went_to_showdown THEN hp.net_won
                       ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN NOT hp.went_to_showdown THEN hp.net_won
                       ELSE 0 END), 0),
    COALESCE(SUM(hp.bb_won), 0),
    COALESCE(SUM(hp.allin_ev_diff), 0),
    COALESCE(SUM(hp.rake_from_won), 0),
    COALESCE(SUM(hp.rake_attributed), 0)
"""


def _background_vacuum() -> None:
    """Run VACUUM ANALYZE on core tables in a background thread."""
    from app.models import _get_engine

    try:
        conn = _get_engine().raw_connection()
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "VACUUM ANALYZE hands, hand_players, actions, "
            "player_cumulative, player_stat_summaries"
        )
        cur.close()
        conn.close()
    except Exception:
        pass  # best-effort; autovacuum will catch up


def purge_hands(
    hero_name: str,
    stakes_list: list[str],
    date_from: date | None = None,
    date_to: date | None = None,
    progress_callback=None,
) -> int:
    """
    Delete hands matching the given stakes and date range for the hero,
    then correct stat summaries and rebuild cumulative P&L.

    Strategy (optimised for large databases):
    1. Collect matching hand IDs in a single indexed query.
    2. Compute stat *deltas* for the hands about to be deleted (4 grouping
       queries scoped only to those IDs — O(deleted), not O(remaining)).
    3. Batch-delete hands in chunks of 1000 so CASCADE work per chunk
       stays bounded and the progress bar can update between chunks.
    4. Subtract deltas from player_stat_summaries; purge any rows that
       drop to zero hands.
    5. Rebuild cumulative P&L via window functions (single INSERT…SELECT).
    6. VACUUM ANALYZE the affected tables so the planner has fresh stats.
    All steps run inside a single transaction (one COMMIT at the end).

    Parameters
    ----------
    hero_name : str
        The hero player name.
    stakes_list : list[str]
        Stakes labels to purge, e.g. ["$0.50/$1.00"].
    date_from, date_to : date | None
        Optional date range bounds (inclusive).
    progress_callback : callable(fraction: float, text: str) | None
        Optional callback invoked at key phases with (fraction, status_text).

    Returns
    -------
    int
        Number of hands deleted.
    """
    from app.importer import _rebuild_cumulative

    def _progress(frac: float, text: str) -> None:
        if progress_callback is not None:
            progress_callback(frac, text)

    # Parse stakes labels into (small_blind, big_blind) tuples
    blind_pairs: list[tuple[Decimal, Decimal]] = []
    for label in stakes_list:
        parts = label.replace("$", "").split("/")
        if len(parts) == 2:
            blind_pairs.append((Decimal(parts[0]), Decimal(parts[1])))

    if not blind_pairs:
        return 0

    _progress(0.02, "Finding matching hands…")

    from app.models import _get_engine
    conn = _get_engine().raw_connection()
    try:
        cur = conn.cursor()

        # ── Look up hero player id ──────────────────────────────────
        cur.execute(
            "SELECT id FROM players WHERE name = %s", (hero_name,),
        )
        row = cur.fetchone()
        if not row:
            return 0
        hero_id: int = row[0]

        # ── Build filter clause ─────────────────────────────────────
        stakes_parts: list[str] = []
        params: list = [hero_id]
        for sb, bb in blind_pairs:
            stakes_parts.append(
                "(h.small_blind = %s AND h.big_blind = %s)"
            )
            params.extend([sb, bb])
        stakes_clause = " OR ".join(stakes_parts)

        where = (
            "hp.player_id = %s AND NOT hp.is_sitting_out "
            f"AND ({stakes_clause})"
        )
        if date_from is not None:
            where += " AND h.played_date >= %s"
            params.append(date_from)
        if date_to is not None:
            where += " AND h.played_date <= %s"
            params.append(date_to)

        # ── Step 1: collect hand IDs into server-side temp table ────
        cur.execute(
            "CREATE TEMP TABLE _purge_ids (id INTEGER NOT NULL) "
            "ON COMMIT DROP"
        )
        cur.execute(
            "INSERT INTO _purge_ids (id) "
            "SELECT DISTINCT h.id FROM hands h "
            "JOIN hand_players hp ON hp.hand_id = h.id "
            f"WHERE {where}",
            params,
        )
        cur.execute("SELECT COUNT(*) FROM _purge_ids")
        total: int = cur.fetchone()[0]

        if total == 0:
            return 0

        # ── Step 2: compute stat deltas BEFORE deleting ─────────────
        _progress(0.05, "Computing stat deltas…")
        _subtract_hero_summaries(cur, hero_id)

        # ── Step 3: batch-delete hands in chunks ────────────────────
        # Drain _purge_ids batch by batch so no Python list is needed.
        # Progress from 0.10 → 0.75 proportional to chunks completed.
        deleted = 0
        while True:
            cur.execute(
                "WITH batch AS ("
                "  DELETE FROM _purge_ids "
                "  WHERE ctid IN ("
                "    SELECT ctid FROM _purge_ids LIMIT %s"
                "  ) RETURNING id"
                ") "
                "DELETE FROM hands WHERE id IN (SELECT id FROM batch)",
                (_DELETE_CHUNK,),
            )
            if cur.rowcount == 0:
                break
            deleted += cur.rowcount
            frac = 0.10 + 0.65 * min(deleted, total) / total
            _progress(
                frac, f"Deleting hands ({min(deleted, total):,}"
                      f"/{total:,})…",
            )

        # ── Step 4: rebuild cumulative P&L ──────────────────────────
        _progress(0.80, "Rebuilding cumulative P&L…")
        cur.close()
        _rebuild_cumulative(conn, hero_id, commit=False, full=True)

        # ── Single commit for the entire operation ──────────────────
        conn.commit()

        # ── Step 5: VACUUM after large deletes (background) ─────────
        if deleted >= 500:
            _progress(0.95, "Scheduling VACUUM…")
            threading.Thread(
                target=_background_vacuum,
                daemon=True,
            ).start()

        _progress(1.0, "Done.")
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Delta-based stat summary correction ────────────────────────────────────

def _subtract_hero_summaries(
    cur, hero_id: int,
) -> None:
    """
    Subtract the stat contribution of hands in the ``_purge_ids``
    temp table from player_stat_summaries for the hero.

    Runs 4 aggregation queries (total / date / stakes / position)
    scoped only to the hands about to be deleted —
    O(len(purge_ids)), NOT O(remaining hands in the DB).

    Then UPDATEs existing summary rows by subtracting those deltas
    and removes any rows whose total_hands drops to zero.
    """
    _BASE_FROM = (
        "FROM hand_players hp "
        "JOIN hands h ON h.id = hp.hand_id "
        "WHERE hp.player_id = %s AND NOT hp.is_sitting_out "
        "AND h.id IN (SELECT id FROM _purge_ids)"
    )
    base_params = (hero_id,)

    # Collect (grouping_type, group_key) → delta-values tuples
    deltas: list[tuple] = []

    # 1. Total aggregate
    cur.execute(
        f"SELECT 'total', 'all', {_DELTA_AGG} {_BASE_FROM}",
        base_params,
    )
    row = cur.fetchone()
    if row and row[2]:  # row[2] = COUNT(*)
        deltas.append(row)

    # 2. By date
    cur.execute(
        f"SELECT 'date', h.played_date::text, {_DELTA_AGG} "
        f"{_BASE_FROM} GROUP BY h.played_date",
        base_params,
    )
    deltas.extend(cur.fetchall())

    # 3. By stakes (group_key matches the import format exactly)
    cur.execute(
        f"SELECT 'stakes', "
        f"  '$' || TRIM(TO_CHAR(h.small_blind, 'FM999990.00')) || "
        f"  '/$' || TRIM(TO_CHAR(h.big_blind, 'FM999990.00')) || "
        f"  ' ' || CASE h.game_type "
        f"    WHEN 'Hold''em No Limit' THEN 'NL Holdem' "
        f"    WHEN 'Omaha Pot Limit' THEN 'PL Omaha' "
        f"    WHEN 'Hold''em Pot Limit' THEN 'PL Holdem' "
        f"    WHEN 'Omaha No Limit' THEN 'NL Omaha' "
        f"    ELSE h.game_type END || "
        f"  '|' || CASE "
        f"    WHEN h.max_seats <= 2 THEN 'HU' "
        f"    WHEN h.max_seats <= 6 THEN '6 Max' "
        f"    WHEN h.max_seats <= 9 THEN '9 Max' "
        f"    ELSE h.max_seats::text || ' Max' END, "
        f"  {_DELTA_AGG} "
        f"{_BASE_FROM} "
        f"GROUP BY h.small_blind, h.big_blind, h.game_type, h.max_seats",
        base_params,
    )
    deltas.extend(cur.fetchall())

    # 4. By position
    cur.execute(
        f"SELECT 'position', "
        f"  CASE hp.position "
        f"    WHEN 'UTG' THEN 'EP' "
        f"    ELSE COALESCE(hp.position, '?') END, "
        f"  {_DELTA_AGG} "
        f"{_BASE_FROM} GROUP BY "
        f"  CASE hp.position WHEN 'UTG' THEN 'EP' "
        f"  ELSE COALESCE(hp.position, '?') END",
        base_params,
    )
    deltas.extend(cur.fetchall())

    if not deltas:
        return

    # Build a single UPDATE for each delta row
    set_clause = ", ".join(
        f"{col} = {col} - %s" for col in _DELTA_COLS
    )
    update_sql = (
        f"UPDATE player_stat_summaries SET {set_clause} "
        f"WHERE player_id = %s AND grouping_type = %s "
        f"AND group_key = %s"
    )

    for row in deltas:
        gtype, gkey = row[0], row[1]
        vals = list(row[2:])  # delta values in _DELTA_COLS order
        cur.execute(update_sql, vals + [hero_id, gtype, gkey])

    # Remove summary rows that now have zero hands
    cur.execute(
        "DELETE FROM player_stat_summaries "
        "WHERE player_id = %s AND total_hands <= 0",
        (hero_id,),
    )
