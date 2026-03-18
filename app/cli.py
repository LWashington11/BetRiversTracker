"""
CLI entry-point for one-off imports, DB initialization, and stat backfill.

Usage:
    python -m app.cli init               # create tables
    python -m app.cli import <path>       # import a file or directory
    python -m app.cli backfill-stats      # recompute all stat flags & aggregates
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.models import init_db
from app.parser import parse_file, parse_files_parallel
from app.importer import import_hands


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m app.cli init            Create DB tables")
        print("  python -m app.cli import <path>    Import hand histories")
        print("  python -m app.cli backfill-stats   Recompute all stat flags & aggregates")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "init":
        print("Creating database tables...")
        init_db()
        _migrate_schema()
        print("Done.")

    elif command == "migrate":
        print("Running schema migrations...")
        init_db()
        _migrate_schema()
        print("Done.")

    elif command == "import":
        if len(sys.argv) < 3:
            print("Please provide a file or directory path.")
            sys.exit(1)

        init_db()
        _migrate_schema()
        target = Path(sys.argv[2])

        if target.is_file():
            print(f"Parsing file: {target}")
            parsed = parse_file(target)
        elif target.is_dir():
            txt_files = sorted(target.rglob("*.txt"))
            if not txt_files:
                print("No .txt files found in directory.")
                sys.exit(0)
            print(
                f"Parsing {len(txt_files)} file(s) from: {target} "
                f"(parallel)..."
            )
            parsed = parse_files_parallel(txt_files)
        else:
            print(f"Path not found: {target}")
            sys.exit(1)

        disable_idx = len(parsed) > 5000
        if disable_idx:
            print(
                "Large batch detected — secondary indexes will be "
                "disabled during import for speed."
            )
        from app.hero_store import get_last_hero
        hero = get_last_hero()
        if hero:
            print(f"Hero: {hero}")
        print(f"Parsed {len(parsed)} hand(s). Importing...")
        imported, skipped = import_hands(
            parsed, hero_name=hero, disable_indexes=disable_idx,
        )
        print(f"Imported: {imported}  |  Skipped (duplicates): {skipped}")

    elif command == "backfill-stats":
        init_db()
        _migrate_schema()
        _backfill_stats()

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


def _migrate_schema() -> None:
    """
    Apply incremental schema changes that ALTER existing tables.
    Uses IF NOT EXISTS / DO NOTHING patterns so it is safe to run
    repeatedly (idempotent).
    """
    from sqlalchemy import text
    from app.models import engine

    # New stat-flag columns added to hand_players in the architecture redesign.
    new_hp_columns = [
        ("was_vpip",                "BOOLEAN DEFAULT FALSE"),
        ("was_pfr",                 "BOOLEAN DEFAULT FALSE"),
        ("was_3bet",                "BOOLEAN DEFAULT FALSE"),
        ("had_3bet_opp",            "BOOLEAN DEFAULT FALSE"),
        ("was_4bet",                "BOOLEAN DEFAULT FALSE"),
        ("had_4bet_opp",            "BOOLEAN DEFAULT FALSE"),
        ("was_rfi",                 "BOOLEAN DEFAULT FALSE"),
        ("had_rfi_opp",             "BOOLEAN DEFAULT FALSE"),
        ("folded_to_3bet",          "BOOLEAN DEFAULT FALSE"),
        ("faced_3bet",              "BOOLEAN DEFAULT FALSE"),
        ("saw_flop",                "BOOLEAN DEFAULT FALSE"),
        ("was_cbet",                "BOOLEAN DEFAULT FALSE"),
        ("had_cbet_opp",            "BOOLEAN DEFAULT FALSE"),
        ("folded_to_btn_steal",     "BOOLEAN DEFAULT FALSE"),
        ("faced_btn_steal",         "BOOLEAN DEFAULT FALSE"),
        ("was_walk",                "BOOLEAN DEFAULT FALSE"),
        ("hero_was_allin",          "BOOLEAN DEFAULT FALSE"),
        ("allin_ev_diff",           "NUMERIC(10,2) DEFAULT 0"),
        ("postflop_bets_raises",    "INTEGER DEFAULT 0"),
        ("postflop_calls",          "INTEGER DEFAULT 0"),
        ("postflop_checks",         "INTEGER DEFAULT 0"),
        ("rake_attributed",         "NUMERIC(10,2) DEFAULT 0"),
        ("rake_from_won",           "NUMERIC(10,2) DEFAULT 0"),
        ("bb_won",                  "NUMERIC(12,4) DEFAULT 0"),
    ]

    with engine.begin() as conn:
        for col_name, col_def in new_hp_columns:
            conn.execute(text(
                f"ALTER TABLE hand_players ADD COLUMN IF NOT EXISTS {col_name} {col_def}"
            ))

        # Ensure the new aggregate tables exist (created by init_db, but
        # guard here in case of an older DB that skipped init after the change).
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS player_stat_summaries (
                id              SERIAL PRIMARY KEY,
                player_id       INTEGER NOT NULL REFERENCES players(id),
                grouping_type   VARCHAR(16) NOT NULL,
                group_key       VARCHAR(64) NOT NULL,
                total_hands     INTEGER DEFAULT 0,
                walk_count      INTEGER DEFAULT 0,
                vpip_count      INTEGER DEFAULT 0,
                pfr_count       INTEGER DEFAULT 0,
                rfi_count       INTEGER DEFAULT 0,
                rfi_opportunities INTEGER DEFAULT 0,
                three_bet_count INTEGER DEFAULT 0,
                three_bet_opportunities INTEGER DEFAULT 0,
                four_bet_count  INTEGER DEFAULT 0,
                four_bet_opportunities INTEGER DEFAULT 0,
                fold_to_3bet_count INTEGER DEFAULT 0,
                fold_to_3bet_opportunities INTEGER DEFAULT 0,
                saw_flop_count  INTEGER DEFAULT 0,
                went_to_sd_count INTEGER DEFAULT 0,
                went_to_sd_from_flop_count INTEGER DEFAULT 0,
                won_at_sd_count INTEGER DEFAULT 0,
                won_when_saw_flop_count INTEGER DEFAULT 0,
                cbet_count      INTEGER DEFAULT 0,
                cbet_opportunities INTEGER DEFAULT 0,
                fold_to_btn_steal_count INTEGER DEFAULT 0,
                fold_to_btn_steal_opportunities INTEGER DEFAULT 0,
                postflop_bets_raises INTEGER DEFAULT 0,
                postflop_calls  INTEGER DEFAULT 0,
                postflop_checks INTEGER DEFAULT 0,
                net_won         NUMERIC(12,2) DEFAULT 0,
                sd_won          NUMERIC(12,2) DEFAULT 0,
                nonsd_won       NUMERIC(12,2) DEFAULT 0,
                bb_won_total    NUMERIC(12,4) DEFAULT 0,
                allin_ev_diff   NUMERIC(12,2) DEFAULT 0,
                rake_from_won   NUMERIC(12,2) DEFAULT 0,
                rake_attributed NUMERIC(12,2) DEFAULT 0,
                UNIQUE(player_id, grouping_type, group_key)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS player_cumulative (
                id                      SERIAL PRIMARY KEY,
                player_id               INTEGER NOT NULL REFERENCES players(id),
                hand_number             INTEGER NOT NULL,
                hand_id                 INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
                played_at               TIMESTAMP NOT NULL,
                net_won_cumulative      NUMERIC(12,2) DEFAULT 0,
                sd_won_cumulative       NUMERIC(12,2) DEFAULT 0,
                nonsd_won_cumulative    NUMERIC(12,2) DEFAULT 0,
                allin_ev_cumulative     NUMERIC(12,2) DEFAULT 0
            )
        """))

        # Indexes (idempotent)
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pss_lookup "
            "ON player_stat_summaries(player_id, grouping_type)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pc_player_hand "
            "ON player_cumulative(player_id, hand_number)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_pc_player_played_at "
            "ON player_cumulative(player_id, played_at)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hp_player_bbwon "
            "ON hand_players(player_id, bb_won) WHERE NOT is_sitting_out"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hp_player_hand_active "
            "ON hand_players(player_id, hand_id) WHERE NOT is_sitting_out"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hands_blinds "
            "ON hands(small_blind, big_blind)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hp_player_position "
            "ON hand_players(player_id, position) WHERE NOT is_sitting_out"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hp_net_won "
            "ON hand_players(net_won)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_act_hp_street_seq "
            "ON actions(hand_player_id, street, sequence)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hands_played_date_id "
            "ON hands(played_date, id)"
        ))
        # Covering index for the slow-path aggregation query so
        # Postgres can use an index-only scan over hand_players.
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_hp_slowpath_cover "
            "ON hand_players(player_id) "
            "INCLUDE (hand_id, position, net_won, bb_won, "
            "  was_vpip, was_pfr, was_3bet, had_3bet_opp, "
            "  was_rfi, had_rfi_opp, saw_flop, went_to_showdown, "
            "  was_cbet, had_cbet_opp, folded_to_3bet, faced_3bet, "
            "  folded_to_btn_steal, faced_btn_steal, was_walk, "
            "  was_4bet, had_4bet_opp, allin_ev_diff, "
            "  postflop_bets_raises, postflop_calls, postflop_checks, "
            "  rake_from_won, rake_attributed) "
            "WHERE NOT is_sitting_out"
        ))

    print("Schema migration complete.")


def _backfill_stats() -> None:
    """
    Recompute all stat flags on hand_players rows and rebuild
    player_stat_summaries and player_cumulative from scratch.

    Processes each player in batches of _BACKFILL_BATCH hands to
    keep memory usage bounded for large databases.
    """
    from decimal import Decimal

    from sqlalchemy import func, text

    from app.models import (
        Hand, Player, HandPlayer, Action, PlayerStatSummary,
        PlayerCumulative, SessionLocal,
    )
    from app.stat_flags import compute_hand_flags, flags_to_summary_deltas
    from app.importer import (
        _stakes_game_key, _position_key, _UPSERT_SQL,
    )

    _BACKFILL_BATCH = 5000  # hands per batch to limit memory

    session = SessionLocal()
    try:
        # ── 1. Truncate aggregation tables ───────────────────────────
        print("Clearing aggregation tables...")
        session.execute(text("DELETE FROM player_cumulative"))
        session.execute(text("DELETE FROM player_stat_summaries"))
        session.commit()

        # ── 2. Load all players ──────────────────────────────────────
        players = session.query(Player).all()
        print(f"Found {len(players)} player(s).")

        for player in players:
            # Count total hands for this player
            total_count = (
                session.query(func.count(HandPlayer.id))
                .join(Hand, Hand.id == HandPlayer.hand_id)
                .filter(
                    HandPlayer.player_id == player.id,
                    HandPlayer.is_sitting_out == False,
                )
                .scalar() or 0
            )
            if total_count == 0:
                continue

            # Cumulative tracking across batches
            cum_net = Decimal("0")
            cum_sd = Decimal("0")
            cum_nonsd = Decimal("0")
            cum_allin_ev = Decimal("0")
            hand_num = 0
            processed = 0

            while processed < total_count:
                # Fetch a batch of hand_player rows ordered chronologically
                hp_rows = (
                    session.query(HandPlayer, Hand)
                    .join(Hand, Hand.id == HandPlayer.hand_id)
                    .filter(
                        HandPlayer.player_id == player.id,
                        HandPlayer.is_sitting_out == False,
                    )
                    .order_by(Hand.played_at, Hand.hand_id)
                    .offset(processed)
                    .limit(_BACKFILL_BATCH)
                    .all()
                )
                if not hp_rows:
                    break

                hand_ids = list({h.id for _, h in hp_rows})

                # Batch fetch actions for this batch only
                all_actions = (
                    session.query(Action)
                    .filter(Action.hand_id.in_(hand_ids))
                    .order_by(Action.hand_id, Action.sequence)
                    .all()
                )
                actions_by_hp: dict[int, list[dict]] = {}
                preflop_by_hand: dict[int, list[dict]] = {}
                for a in all_actions:
                    act_dict = {
                        "street": a.street,
                        "sequence": a.sequence,
                        "action_type": a.action_type,
                        "amount": a.amount or Decimal("0"),
                        "raise_to": a.raise_to,
                        "is_all_in": a.is_all_in or False,
                    }
                    actions_by_hp.setdefault(
                        a.hand_player_id, [],
                    ).append(act_dict)

                    if a.street == "PREFLOP":
                        preflop_by_hand.setdefault(
                            a.hand_id, [],
                        ).append({
                            "hand_player_id": a.hand_player_id,
                            "sequence": a.sequence,
                            "action_type": a.action_type,
                            "amount": a.amount or Decimal("0"),
                            "is_all_in": a.is_all_in or False,
                        })

                for acts in preflop_by_hand.values():
                    acts.sort(key=lambda a: a["sequence"])

                # BTN and showdown counts for this batch
                btn_by_hand: dict[int, int | None] = {}
                sd_count_by_hand: dict[int, int] = {}
                all_hps_by_hand: dict[int, list[HandPlayer]] = {}

                all_hps = (
                    session.query(HandPlayer)
                    .filter(HandPlayer.hand_id.in_(hand_ids))
                    .all()
                )
                for hp_r in all_hps:
                    all_hps_by_hand.setdefault(
                        hp_r.hand_id, [],
                    ).append(hp_r)
                    if hp_r.position == "BTN":
                        btn_by_hand[hp_r.hand_id] = hp_r.id

                for hid, hps in all_hps_by_hand.items():
                    sd_count_by_hand[hid] = sum(
                        1 for h in hps if h.went_to_showdown
                    )

                batch_cumulative = []

                for hp, hand in hp_rows:
                    hand_num += 1

                    flags = compute_hand_flags(
                        hp_id=hp.id,
                        position=hp.position or "",
                        net_won=hp.net_won or Decimal("0"),
                        won_amount=hp.won_amount or Decimal("0"),
                        total_invested=hp.total_invested or Decimal("0"),
                        went_to_showdown=hp.went_to_showdown or False,
                        big_blind=hand.big_blind or Decimal("1"),
                        total_pot=hand.total_pot or Decimal("0"),
                        rake=hand.rake or Decimal("0"),
                        stp_amount=hand.stp_amount or Decimal("0"),
                        board=hand.board,
                        hero_actions=actions_by_hp.get(hp.id, []),
                        all_preflop_actions=preflop_by_hand.get(
                            hand.id, [],
                        ),
                        sd_player_count=sd_count_by_hand.get(
                            hand.id, 1,
                        ),
                        btn_hp_id=btn_by_hand.get(hand.id),
                    )

                    # Update hand_player flags
                    hp.was_vpip = flags["was_vpip"]
                    hp.was_pfr = flags["was_pfr"]
                    hp.was_3bet = flags["was_3bet"]
                    hp.had_3bet_opp = flags["had_3bet_opp"]
                    hp.was_4bet = flags["was_4bet"]
                    hp.had_4bet_opp = flags["had_4bet_opp"]
                    hp.was_rfi = flags["was_rfi"]
                    hp.had_rfi_opp = flags["had_rfi_opp"]
                    hp.folded_to_3bet = flags["folded_to_3bet"]
                    hp.faced_3bet = flags["faced_3bet"]
                    hp.saw_flop = flags["saw_flop"]
                    hp.was_cbet = flags["was_cbet"]
                    hp.had_cbet_opp = flags["had_cbet_opp"]
                    hp.folded_to_btn_steal = flags["folded_to_btn_steal"]
                    hp.faced_btn_steal = flags["faced_btn_steal"]
                    hp.was_walk = flags["was_walk"]
                    hp.hero_was_allin = flags["hero_was_allin"]
                    hp.allin_ev_diff = flags["allin_ev_diff"]
                    hp.postflop_bets_raises = flags["postflop_bets_raises"]
                    hp.postflop_calls = flags["postflop_calls"]
                    hp.postflop_checks = flags["postflop_checks"]
                    hp.rake_attributed = flags["rake_attributed"]
                    hp.rake_from_won = flags["rake_from_won"]
                    hp.bb_won = flags["bb_won"]

                    # Upsert aggregation summaries
                    deltas = flags_to_summary_deltas(flags)
                    groupings = [
                        ("total", "all"),
                        ("date", str(hand.played_date)),
                        ("stakes", _stakes_game_key(hand)),
                        ("position", _position_key(hp)),
                    ]
                    for grouping_type, group_key in groupings:
                        params = {
                            "player_id": player.id,
                            "grouping_type": grouping_type,
                            "group_key": group_key,
                        }
                        params.update(deltas)
                        session.execute(_UPSERT_SQL, params)

                    # Cumulative P&L
                    net = hp.net_won or Decimal("0")
                    cum_net += net
                    went_sd = hp.went_to_showdown or False
                    if went_sd:
                        cum_sd += net
                    else:
                        cum_nonsd += net
                    cum_allin_ev += flags["allin_ev_diff"]

                    batch_cumulative.append(PlayerCumulative(
                        player_id=player.id,
                        hand_number=hand_num,
                        hand_id=hand.id,
                        played_at=hand.played_at,
                        net_won_cumulative=cum_net,
                        sd_won_cumulative=cum_sd,
                        nonsd_won_cumulative=cum_nonsd,
                        allin_ev_cumulative=cum_net + cum_allin_ev,
                    ))

                session.add_all(batch_cumulative)
                session.flush()

                processed += len(hp_rows)
                print(
                    f"  {player.name}: {processed}/{total_count} "
                    f"hands processed…"
                )

                # Evict ORM identity map to free memory between batches
                session.expire_all()

            # Commit after each player so progress is durable
            session.commit()
            print(
                f"  {player.name}: {hand_num} hands complete, "
                f"flags + aggregates updated."
            )

        print("Backfill complete.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
