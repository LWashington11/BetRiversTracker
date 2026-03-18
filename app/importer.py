"""
Importer: high-throughput bulk import of parsed hand data into PostgreSQL.

Uses psycopg2 execute_values and COPY for maximum insert throughput.
Stat flags are computed in Python before insertion; stat summaries are
accumulated in-memory and upserted in a single batch per chunk.

Optimizations applied:
  1. execute_values multi-row INSERT for hands and hand_players
  2. COPY FROM STDIN for actions (highest-volume table)
  3. Stat summary deltas accumulated in Python, batch-upserted once per chunk
  4. All flush() / per-row round-trips eliminated — IDs resolved via RETURNING
  5. Chunked commits (configurable, default 1000 hands per transaction)
  6. Optional secondary index disable/rebuild for large imports
"""

from __future__ import annotations

import io
from decimal import Decimal
from itertools import islice
from typing import Any, Callable, Iterable

import psycopg2
from psycopg2.extras import execute_values
from sqlalchemy import text

from app.config import DATABASE_URL
from app.constants import GAME_TYPE_SHORT, POSITION_DISPLAY, IMPORT_CHUNK_SIZE
from app.stat_flags import compute_hand_flags, flags_to_summary_deltas


# ── Constants ────────────────────────────────────────────────────────────────

CHUNK_SIZE = IMPORT_CHUNK_SIZE
_ZERO = Decimal("0")


def _stakes_game_key(hand) -> str:
    """e.g. '$0.50/$1.00 NL Holdem|6 Max'.  Accepts ORM Hand or dict."""
    if isinstance(hand, dict):
        sb = float(hand.get("small_blind") or 0)
        bb = float(hand.get("big_blind") or 0)
        game_type = hand.get("game_type", "")
        max_seats = hand.get("max_seats") or 0
    else:
        sb = float(hand.small_blind or 0)
        bb = float(hand.big_blind or 0)
        game_type = hand.game_type or ""
        max_seats = hand.max_seats or 0
    game = GAME_TYPE_SHORT.get(game_type, game_type)
    if max_seats <= 2:
        seats_label = "HU"
    elif max_seats <= 6:
        seats_label = "6 Max"
    elif max_seats <= 9:
        seats_label = "9 Max"
    else:
        seats_label = f"{max_seats} Max"
    return f"${sb:.2f}/${bb:.2f} {game}|{seats_label}"


def _position_key(hp_or_pos) -> str:
    """Position display key.  Accepts ORM HandPlayer or position string."""
    if hasattr(hp_or_pos, "position"):
        pos = hp_or_pos.position or "?"
    else:
        pos = hp_or_pos or "?"
    return POSITION_DISPLAY.get(pos, pos)


# ── Summary delta columns (shared with backfill via _UPSERT_SQL) ────────────

_SUMMARY_DELTA_COLS = [
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
]

# ── SQLAlchemy upsert SQL (used by backfill in cli.py) ──────────────────────

_SET_CLAUSE = ", ".join(
    f"{col} = player_stat_summaries.{col} + EXCLUDED.{col}"
    for col in _SUMMARY_DELTA_COLS
)
_COL_LIST = ", ".join(
    ["player_id", "grouping_type", "group_key"] + _SUMMARY_DELTA_COLS
)
_PARAM_LIST = ", ".join(
    [":player_id", ":grouping_type", ":group_key"]
    + [f":{col}" for col in _SUMMARY_DELTA_COLS]
)
_UPSERT_SQL = text(
    f"INSERT INTO player_stat_summaries ({_COL_LIST}) "
    f"VALUES ({_PARAM_LIST}) "
    f"ON CONFLICT (player_id, grouping_type, group_key) "
    f"DO UPDATE SET {_SET_CLAUSE}"
)

# ── psycopg2 batch upsert SQL for stat summaries ────────────────────────────

_PG_SUMMARY_COLS = (
    ["player_id", "grouping_type", "group_key"] + _SUMMARY_DELTA_COLS
)
_PG_SUMMARY_UPSERT = (
    f"INSERT INTO player_stat_summaries ({', '.join(_PG_SUMMARY_COLS)}) "
    f"VALUES %s "
    f"ON CONFLICT (player_id, grouping_type, group_key) "
    f"DO UPDATE SET {_SET_CLAUSE}"
)

# ── Column lists for bulk inserts ────────────────────────────────────────────

_HAND_COLS = [
    "hand_id", "game_type", "small_blind", "big_blind", "table_id",
    "max_seats", "button_seat", "played_at", "played_date", "board",
    "total_pot", "main_pot", "stp_amount", "rake", "raw_text",
]

_HP_COLS = [
    "hand_id", "player_id", "seat", "stack", "position", "is_sitting_out",
    "hole_cards", "won_amount", "net_won", "total_invested",
    "showed_hand", "went_to_showdown",
    "was_vpip", "was_pfr", "was_3bet", "had_3bet_opp",
    "was_4bet", "had_4bet_opp", "was_rfi", "had_rfi_opp",
    "folded_to_3bet", "faced_3bet", "saw_flop",
    "was_cbet", "had_cbet_opp",
    "folded_to_btn_steal", "faced_btn_steal", "was_walk",
    "hero_was_allin", "allin_ev_diff",
    "postflop_bets_raises", "postflop_calls", "postflop_checks",
    "rake_attributed", "rake_from_won", "bb_won",
]

# Flag columns copied from compute_hand_flags() onto the hp dict
_FLAG_COLS = [
    "was_vpip", "was_pfr", "was_3bet", "had_3bet_opp",
    "was_4bet", "had_4bet_opp", "was_rfi", "had_rfi_opp",
    "folded_to_3bet", "faced_3bet", "saw_flop",
    "was_cbet", "had_cbet_opp",
    "folded_to_btn_steal", "faced_btn_steal", "was_walk",
    "hero_was_allin", "allin_ev_diff",
    "postflop_bets_raises", "postflop_calls", "postflop_checks",
    "rake_attributed", "rake_from_won", "bb_won",
]

# Default values for flag columns — used for opponent hand_players rows.
# Opponents have randomised names and are never queried for stats, so
# compute_hand_flags is skipped for them entirely.
_FLAG_DEFAULTS: dict[str, Any] = {
    "was_vpip": False, "was_pfr": False,
    "was_3bet": False, "had_3bet_opp": False,
    "was_4bet": False, "had_4bet_opp": False,
    "was_rfi": False, "had_rfi_opp": False,
    "folded_to_3bet": False, "faced_3bet": False,
    "saw_flop": False, "was_cbet": False, "had_cbet_opp": False,
    "folded_to_btn_steal": False, "faced_btn_steal": False,
    "was_walk": False, "hero_was_allin": False,
    "allin_ev_diff": Decimal("0"),
    "postflop_bets_raises": 0, "postflop_calls": 0, "postflop_checks": 0,
    "rake_attributed": Decimal("0"),
    "rake_from_won": Decimal("0"),
    "bb_won": Decimal("0"),
}

_HAND_TMPL = "(" + ", ".join(f"%({c})s" for c in _HAND_COLS) + ")"
_HP_TMPL = "(" + ", ".join(f"%({c})s" for c in _HP_COLS) + ")"


# ── Secondary indexes managed during bulk imports ────────────────────────────

_IDX_TO_DROP = [
    "idx_hands_played_at", "ix_hands_played_at",
    "idx_hands_played_date", "ix_hands_played_date",
    "ix_hands_blinds", "ix_hands_played_date_id",
    "ix_hp_player_active", "ix_hp_hand_showdown",
    "ix_hp_player_bbwon", "ix_hp_player_position",
    "ix_hp_player_hand_active", "ix_hp_net_won",
    "ix_act_hp_allin", "ix_act_hand_street_seq", "ix_act_hp_street_seq",
    "ix_pss_lookup",
    "ix_pc_player_hand", "ix_pc_player_played_at",
]

_IDX_TO_CREATE = [
    "CREATE INDEX IF NOT EXISTS idx_hands_played_at"
    " ON hands(played_at)",
    "CREATE INDEX IF NOT EXISTS idx_hands_played_date"
    " ON hands(played_date)",
    "CREATE INDEX IF NOT EXISTS ix_hands_blinds"
    " ON hands(small_blind, big_blind)",
    "CREATE INDEX IF NOT EXISTS ix_hands_played_date_id"
    " ON hands(played_date, id)",
    "CREATE INDEX IF NOT EXISTS ix_hp_player_active"
    " ON hand_players(player_id, is_sitting_out)",
    "CREATE INDEX IF NOT EXISTS ix_hp_hand_showdown"
    " ON hand_players(hand_id, went_to_showdown)",
    "CREATE INDEX IF NOT EXISTS ix_hp_player_bbwon"
    " ON hand_players(player_id, bb_won) WHERE NOT is_sitting_out",
    "CREATE INDEX IF NOT EXISTS ix_hp_player_position"
    " ON hand_players(player_id, position) WHERE NOT is_sitting_out",
    "CREATE INDEX IF NOT EXISTS ix_hp_player_hand_active"
    " ON hand_players(player_id, hand_id) WHERE NOT is_sitting_out",
    "CREATE INDEX IF NOT EXISTS ix_hp_net_won"
    " ON hand_players(net_won)",
    "CREATE INDEX IF NOT EXISTS ix_act_hp_allin"
    " ON actions(hand_player_id, is_all_in)",
    "CREATE INDEX IF NOT EXISTS ix_act_hand_street_seq"
    " ON actions(hand_id, street, sequence)",
    "CREATE INDEX IF NOT EXISTS ix_act_hp_street_seq"
    " ON actions(hand_player_id, street, sequence)",
    "CREATE INDEX IF NOT EXISTS ix_pss_lookup"
    " ON player_stat_summaries(player_id, grouping_type)",
    "CREATE INDEX IF NOT EXISTS ix_pc_player_hand"
    " ON player_cumulative(player_id, hand_number)",
    "CREATE INDEX IF NOT EXISTS ix_pc_player_played_at"
    " ON player_cumulative(player_id, played_at)",
]


# ── Utility ──────────────────────────────────────────────────────────────────

def _chunked(iterable, size: int):
    """Yield successive chunks from an iterable."""
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            break
        yield chunk


def _get_conn():
    """Open a raw psycopg2 connection from the SQLAlchemy pool."""
    from app.models import _get_engine
    return _get_engine().raw_connection()


# ── Bulk helpers ─────────────────────────────────────────────────────────────

def _resolve_players(cur, names: set[str]) -> dict[str, int]:
    """Ensure all player names exist in DB; return name → id mapping."""
    if not names:
        return {}
    names_list = list(names)
    # Insert any missing players (no-op for existing ones)
    execute_values(
        cur,
        "INSERT INTO players (name) VALUES %s "
        "ON CONFLICT (name) DO NOTHING",
        [(n,) for n in names_list],
        page_size=1000,
    )
    # Fetch all IDs in one round-trip
    cur.execute(
        "SELECT name, id FROM players WHERE name = ANY(%s)",
        (names_list,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _bulk_insert_hands(cur, rows: list[dict]) -> dict[int, int]:
    """
    Bulk INSERT hands via execute_values.

    Returns {original_hand_id: database_id}.
    """
    if not rows:
        return {}
    results = execute_values(
        cur,
        f"INSERT INTO hands ({', '.join(_HAND_COLS)}) VALUES %s "
        f"RETURNING hand_id, id",
        rows,
        template=_HAND_TMPL,
        page_size=CHUNK_SIZE,
        fetch=True,
    )
    return {r[0]: r[1] for r in results}


def _bulk_insert_hps(cur, rows: list[dict]) -> dict[tuple[int, int], int]:
    """
    Bulk INSERT hand_players via execute_values.

    Returns {(hand_db_id, player_id): hand_player_id}.
    """
    if not rows:
        return {}
    results = execute_values(
        cur,
        f"INSERT INTO hand_players ({', '.join(_HP_COLS)}) VALUES %s "
        f"RETURNING hand_id, player_id, id",
        rows,
        template=_HP_TMPL,
        page_size=CHUNK_SIZE,
        fetch=True,
    )
    return {(r[0], r[1]): r[2] for r in results}


def _copy_actions(cur, rows: list[tuple]) -> None:
    """Bulk-insert actions via COPY FROM STDIN (fastest path)."""
    if not rows:
        return
    buf = io.StringIO()
    for (hand_id, hp_id, street, seq, atype, amt, rto, allin) in rows:
        parts = [
            str(hand_id),
            str(hp_id),
            street,
            str(seq),
            atype,
            str(amt),
            str(rto) if rto is not None else "\\N",
            "t" if allin else "f",
        ]
        buf.write("\t".join(parts))
        buf.write("\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY actions (hand_id, hand_player_id, street, sequence, "
        "action_type, amount, raise_to, is_all_in) FROM STDIN",
        buf,
    )


def _batch_upsert_summaries(cur, acc: dict[tuple, dict]) -> None:
    """Batch-upsert all accumulated stat summary deltas in one call."""
    if not acc:
        return
    rows = []
    for (pid, gtype, gkey), deltas in acc.items():
        row = (pid, gtype, gkey) + tuple(
            deltas.get(c, 0) for c in _SUMMARY_DELTA_COLS
        )
        rows.append(row)
    execute_values(cur, _PG_SUMMARY_UPSERT, rows, page_size=500)


def _rebuild_cumulative(
    conn, hero_id: int, *, commit: bool = True, full: bool = False,
) -> None:
    """
    Rebuild the player_cumulative table for the hero.

    By default performs an **incremental** append: finds the last
    cumulative row, reads its running totals and hand_number, then
    inserts only the newly-imported hands.  This is O(new_hands)
    instead of O(total_hands).

    Parameters
    ----------
    conn : psycopg2 connection
    hero_id : int
    commit : bool
        If True (default), commit the transaction.  Pass False when the
        caller manages the transaction (e.g. purge_hands).
    full : bool
        If True, delete all existing rows and rebuild from scratch.
        Required after deletes (purge) because hand numbering changes.
    """
    cur = conn.cursor()
    try:
        if full:
            # Full rebuild: DELETE + re-INSERT everything
            cur.execute(
                "DELETE FROM player_cumulative WHERE player_id = %s",
                (hero_id,),
            )
            cur.execute(
                """
                INSERT INTO player_cumulative
                    (player_id, hand_number, hand_id, played_at,
                     net_won_cumulative, sd_won_cumulative,
                     nonsd_won_cumulative, allin_ev_cumulative)
                SELECT
                    %s,
                    ROW_NUMBER() OVER (ORDER BY h.played_at, h.id),
                    h.id,
                    h.played_at,
                    SUM(hp.net_won) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    SUM(
                        CASE WHEN hp.went_to_showdown THEN hp.net_won
                             ELSE 0 END
                    ) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    SUM(
                        CASE WHEN NOT hp.went_to_showdown THEN hp.net_won
                             ELSE 0 END
                    ) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    SUM(hp.allin_ev_diff) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )
                FROM hand_players hp
                JOIN hands h ON h.id = hp.hand_id
                WHERE hp.player_id = %s
                  AND NOT hp.is_sitting_out
                ORDER BY h.played_at, h.id
                """,
                (hero_id, hero_id),
            )
        else:
            # Incremental append: only insert rows for hands newer
            # than the last cumulative entry.
            cur.execute(
                "SELECT hand_number, hand_id, played_at, "
                "       net_won_cumulative, sd_won_cumulative, "
                "       nonsd_won_cumulative, allin_ev_cumulative "
                "FROM player_cumulative "
                "WHERE player_id = %s "
                "ORDER BY hand_number DESC LIMIT 1",
                (hero_id,),
            )
            last = cur.fetchone()

            if last is None:
                # No existing cumulative rows — do a full build
                _rebuild_cumulative(
                    conn, hero_id, commit=commit, full=True,
                )
                return

            last_hand_num = last[0]
            last_hand_id = last[1]
            last_played_at = last[2]
            base_net = last[3]
            base_sd = last[4]
            base_nonsd = last[5]
            base_ev = last[6]

            # Insert only hands that come after the last cumulative hand.
            # The WHERE clause mirrors the ORDER BY (played_at, h.id)
            # used in the full rebuild, so new hands sort after the last.
            cur.execute(
                """
                INSERT INTO player_cumulative
                    (player_id, hand_number, hand_id, played_at,
                     net_won_cumulative, sd_won_cumulative,
                     nonsd_won_cumulative, allin_ev_cumulative)
                SELECT
                    %s,
                    %s + ROW_NUMBER() OVER (ORDER BY h.played_at, h.id),
                    h.id,
                    h.played_at,
                    %s + SUM(hp.net_won) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    %s + SUM(
                        CASE WHEN hp.went_to_showdown THEN hp.net_won
                             ELSE 0 END
                    ) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    %s + SUM(
                        CASE WHEN NOT hp.went_to_showdown THEN hp.net_won
                             ELSE 0 END
                    ) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    %s + SUM(hp.allin_ev_diff) OVER (
                        ORDER BY h.played_at, h.id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )
                FROM hand_players hp
                JOIN hands h ON h.id = hp.hand_id
                WHERE hp.player_id = %s
                  AND NOT hp.is_sitting_out
                  AND (h.played_at, h.id) > (%s, %s)
                ORDER BY h.played_at, h.id
                """,
                (
                    hero_id,
                    last_hand_num,
                    base_net,
                    base_sd,
                    base_nonsd,
                    base_ev,
                    hero_id,
                    last_played_at,
                    last_hand_id,
                ),
            )
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def _drop_indexes(conn) -> None:
    """Drop secondary indexes to speed up bulk inserts."""
    cur = conn.cursor()
    for name in _IDX_TO_DROP:
        cur.execute(f"DROP INDEX IF EXISTS {name}")
    conn.commit()
    cur.close()


def _rebuild_indexes(conn) -> None:
    """Recreate secondary indexes after bulk import.

    Uses CONCURRENTLY so reads/writes aren't blocked during index
    creation.  CONCURRENTLY requires autocommit (no open transaction).
    """
    old_autocommit = conn.autocommit
    conn.autocommit = True
    cur = conn.cursor()
    try:
        for ddl in _IDX_TO_CREATE:
            cur.execute(ddl.replace(
                "CREATE INDEX IF NOT EXISTS",
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS",
            ))
    finally:
        cur.close()
        conn.autocommit = old_autocommit


# ── Core chunk import ────────────────────────────────────────────────────────

def _import_chunk(
    conn,
    hands: list[dict[str, Any]],
    hero_name: str | None = None,
) -> tuple[int, int]:
    """
    Import a chunk of parsed hands in a single transaction.

    Only the hero's stat flags and stat summaries are computed/persisted.
    Opponents (random anonymous names) get hand_player rows for the
    replayer but no stat computation.

    Returns (imported_count, skipped_count).
    """
    cur = conn.cursor()
    try:
        # ── 1. Bulk duplicate check ─────────────────────────────────
        candidate_ids = [d["hand_id"] for d in hands]
        cur.execute(
            "SELECT hand_id FROM hands WHERE hand_id = ANY(%s)",
            (candidate_ids,),
        )
        existing_ids = {r[0] for r in cur.fetchall()}

        # Deduplicate within the batch as well
        seen: set[int] = set()
        new_hands: list[dict] = []
        for d in hands:
            hid = d["hand_id"]
            if hid not in existing_ids and hid not in seen:
                seen.add(hid)
                new_hands.append(d)
        skipped = len(hands) - len(new_hands)

        if not new_hands:
            return 0, skipped

        # ── 2. Resolve all player names → IDs in bulk ───────────────
        all_names: set[str] = set()
        for d in new_hands:
            for info in d.get("seats", {}).values():
                if not info.get("is_sitting_out"):
                    all_names.add(info["name"])
        name_to_pid = _resolve_players(cur, all_names)

        # ── 3. Prepare all rows + compute flags (pure Python) ───────
        hand_rows: list[dict] = []
        hp_rows: list[dict] = []
        action_staging: list[tuple] = []
        summary_acc: dict[tuple, dict] = {}

        temp_counter = 0  # local IDs for flag computation

        for d in new_hands:
            orig_hid = d["hand_id"]

            # Hand row
            hand_rows.append({
                "hand_id": orig_hid,
                "game_type": d["game_type"],
                "small_blind": d["small_blind"],
                "big_blind": d["big_blind"],
                "table_id": d.get("table_id"),
                "max_seats": d.get("max_seats", 6),
                "button_seat": d.get("button_seat"),
                "played_at": d["played_at"],
                "played_date": d["played_date"],
                "board": d.get("board"),
                "total_pot": d.get("total_pot", _ZERO),
                "main_pot": d.get("main_pot", _ZERO),
                "stp_amount": d.get("stp_amount", _ZERO),
                "rake": d.get("rake", _ZERO),
                "raw_text": d.get("raw_text"),
            })

            # Process seats → hand_player dicts
            seats = d.get("seats", {})

            # Identify the hero for this hand: prefer explicit hero_name;
            # fall back to the player dealt hole cards (only visible for
            # the account owner in BetRivers hand histories).
            hero_in_hand: str | None = hero_name
            if hero_in_hand is None:
                hero_in_hand = next(
                    (
                        info["name"]
                        for info in seats.values()
                        if info.get("hole_cards") is not None
                        and not info.get("is_sitting_out")
                    ),
                    None,
                )

            name_to_temp: dict[str, int] = {}
            temp_hps: list[dict] = []
            btn_temp_id: int | None = None

            for seat_num, info in seats.items():
                if info.get("is_sitting_out"):
                    continue

                temp_counter += 1
                temp_id = temp_counter
                pname = info["name"]
                name_to_temp[pname] = temp_id

                hp = {
                    # Metadata (not DB columns — ignored by template)
                    "_temp_id": temp_id,
                    "_orig_hand_id": orig_hid,
                    "_is_hero": pname == hero_in_hand,
                    # DB columns (hand_id set after hands INSERT)
                    "player_id": name_to_pid[pname],
                    "seat": seat_num,
                    "stack": info["stack"],
                    "position": info.get("position"),
                    "is_sitting_out": False,
                    "hole_cards": info.get("hole_cards"),
                    "won_amount": info.get("won_amount", _ZERO),
                    "net_won": info.get("net_won", _ZERO),
                    "total_invested": info.get(
                        "total_invested", _ZERO,
                    ),
                    "showed_hand": info.get("showed_hand", False),
                    "went_to_showdown": info.get(
                        "went_to_showdown", False,
                    ),
                    # Stat flags default to zero; overridden for hero below
                    **_FLAG_DEFAULTS,
                }
                temp_hps.append(hp)

                if info.get("position") == "BTN":
                    btn_temp_id = temp_id

            # Build action structures with temp IDs
            actions_by_temp: dict[int, list[dict]] = {}
            all_preflop: list[dict] = []

            for act in d.get("actions", []):
                temp_id = name_to_temp.get(act["player_name"])
                if temp_id is None:
                    continue  # sitting-out or unknown

                act_dict = {
                    "street": act["street"],
                    "sequence": act["sequence"],
                    "action_type": act["action_type"],
                    "amount": act.get("amount", _ZERO),
                    "raise_to": act.get("raise_to"),
                    "is_all_in": act.get("is_all_in", False),
                }
                actions_by_temp.setdefault(
                    temp_id, [],
                ).append(act_dict)

                if act["street"] == "PREFLOP":
                    all_preflop.append({
                        "hand_player_id": temp_id,
                        "sequence": act["sequence"],
                        "action_type": act["action_type"],
                        "amount": act.get("amount", _ZERO),
                        "is_all_in": act.get("is_all_in", False),
                    })

                # Stage for COPY (resolved to DB IDs after inserts)
                action_staging.append((
                    orig_hid,
                    act["player_name"],
                    act["street"],
                    act["sequence"],
                    act["action_type"],
                    act.get("amount", _ZERO),
                    act.get("raise_to"),
                    act.get("is_all_in", False),
                ))

            all_preflop.sort(key=lambda a: a["sequence"])
            sd_player_count = sum(
                1 for hp in temp_hps if hp["went_to_showdown"]
            )

            # Compute stat flags only for the hero.
            # Opponents have randomised site-generated names and are never
            # queried for statistics, so skipping them saves ~5x compute_hand_flags
            # calls and ~20 stat summary upserts per hand at a 6-max table.
            for hp in temp_hps:
                if hp["_is_hero"]:
                    flags = compute_hand_flags(
                        hp_id=hp["_temp_id"],
                        position=hp["position"] or "",
                        net_won=hp["net_won"] or _ZERO,
                        won_amount=hp["won_amount"] or _ZERO,
                        total_invested=hp["total_invested"] or _ZERO,
                        went_to_showdown=hp["went_to_showdown"],
                        big_blind=d["big_blind"] or Decimal("1"),
                        total_pot=d.get("total_pot") or _ZERO,
                        rake=d.get("rake") or _ZERO,
                        stp_amount=d.get("stp_amount") or _ZERO,
                        board=d.get("board"),
                        hero_actions=actions_by_temp.get(
                            hp["_temp_id"], [],
                        ),
                        all_preflop_actions=all_preflop,
                        sd_player_count=sd_player_count,
                        btn_hp_id=btn_temp_id,
                    )

                    # Override flag defaults with computed values
                    for col in _FLAG_COLS:
                        hp[col] = flags[col]

                    # Accumulate stat summary deltas for the hero only
                    deltas = flags_to_summary_deltas(flags)
                    groupings = [
                        ("total", "all"),
                        ("date", str(d["played_date"])),
                        ("stakes", _stakes_game_key(d)),
                        ("position", _position_key(
                            hp["position"] or "?",
                        )),
                    ]
                    for gtype, gkey in groupings:
                        key = (hp["player_id"], gtype, gkey)
                        if key not in summary_acc:
                            summary_acc[key] = dict(deltas)
                        else:
                            acc_row = summary_acc[key]
                            for c, v in deltas.items():
                                acc_row[c] = acc_row[c] + v

                hp_rows.append(hp)

        # ── 4. Bulk INSERT hands ─────────────────────────────────────
        hid_to_dbid = _bulk_insert_hands(cur, hand_rows)

        # ── 5. Set DB hand_id on hp rows, then bulk INSERT ──────────
        for hp in hp_rows:
            hp["hand_id"] = hid_to_dbid[hp["_orig_hand_id"]]
        hp_key_to_dbid = _bulk_insert_hps(cur, hp_rows)

        # ── 6. Resolve action DB IDs and COPY ────────────────────────
        resolved_actions: list[tuple] = []
        for (
            orig_hid, pname, street, seq,
            atype, amount, raise_to, is_allin,
        ) in action_staging:
            hand_db_id = hid_to_dbid.get(orig_hid)
            if hand_db_id is None:
                continue
            pid = name_to_pid.get(pname)
            if pid is None:
                continue
            hp_db_id = hp_key_to_dbid.get((hand_db_id, pid))
            if hp_db_id is None:
                continue
            resolved_actions.append((
                hand_db_id, hp_db_id, street, seq,
                atype, amount, raise_to, is_allin,
            ))
        _copy_actions(cur, resolved_actions)

        # ── 7. Batch upsert stat summaries ───────────────────────────
        _batch_upsert_summaries(cur, summary_acc)

        conn.commit()
        return len(new_hands), skipped

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


# ── Public API ───────────────────────────────────────────────────────────────

def import_hands(
    parsed_hands: Iterable[dict[str, Any]],
    *,
    hero_name: str | None = None,
    chunk_size: int = CHUNK_SIZE,
    disable_indexes: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """
    Import parsed hand dicts using high-throughput bulk operations.

    Parameters
    ----------
    parsed_hands : iterable of dict
        Parsed hand dicts (from parser.parse_file / parse_hand).
    hero_name : str, optional
        The account owner's player name.  Stat flags and stat summaries
        are only computed for this player.  When omitted, the hero is
        auto-detected per hand from the "Dealt to" hole-cards entry.
    chunk_size : int
        Number of hands per transaction chunk (default 1000).
    disable_indexes : bool
        If True, drop secondary indexes before import and rebuild after.
        Recommended for batches > 5000 hands.
    progress_callback : callable, optional
        Called after each chunk with (total_imported, total_skipped).

    Returns
    -------
    (imported_count, skipped_duplicates)
    """
    total_imported = 0
    total_skipped = 0

    conn = _get_conn()
    try:
        if disable_indexes:
            _drop_indexes(conn)

        for chunk in _chunked(parsed_hands, chunk_size):
            imported, skipped = _import_chunk(conn, chunk, hero_name)
            total_imported += imported
            total_skipped += skipped
            if progress_callback:
                progress_callback(total_imported, total_skipped)

        # Rebuild cumulative P&L table for the hero after all chunks.
        # Uses the same connection so the rebuild is atomic with the
        # import — if it fails, all imported hands are also rolled back.
        if hero_name and total_imported > 0:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM players WHERE name = %s", (hero_name,),
            )
            row = cur.fetchone()
            cur.close()
            if row:
                _rebuild_cumulative(conn, row[0])
    finally:
        try:
            if disable_indexes:
                _rebuild_indexes(conn)
        finally:
            conn.close()

    return total_imported, total_skipped
