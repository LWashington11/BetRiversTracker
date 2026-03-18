-- ═══════════════════════════════════════════════════════════════════════════
-- Performance indexes for the Hands-in-Report grid.
--
-- These complement the existing indexes in schema.sql and target the
-- specific query pattern used by hands_repository.py:
--
--     SELECT hp.*, h.*
--     FROM   hand_players hp
--     JOIN   hands h ON h.id = hp.hand_id
--     WHERE  hp.player_id = :hero  AND  NOT hp.is_sitting_out
--            AND [position / stakes / date filters]
--     ORDER BY h.played_at DESC
--     LIMIT :limit  OFFSET :offset
--
-- Strategy:
--   1. Covering partial index on hand_players for the hero's active rows.
--   2. Composite index on (small_blind, big_blind) for stake filtering.
--   3. Partial index on (player_id, position) for position filtering.
--   4. Index on hand_players.net_won for sorting by profit.
--   5. Composite on actions for the batch-fetch of hero actions.
-- ═══════════════════════════════════════════════════════════════════════════

-- 1. Hero-centric hand lookup (covers the WHERE + ORDER BY join)
--    Partial index excludes sitting-out rows to keep it compact.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hp_player_hand_active
    ON hand_players (player_id, hand_id)
    WHERE NOT is_sitting_out;

-- 2. Stake pair filtering (used in OR conditions on small_blind + big_blind)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hands_blinds
    ON hands (small_blind, big_blind);

-- 3. Position filtering for a specific player
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hp_player_position
    ON hand_players (player_id, position)
    WHERE NOT is_sitting_out;

-- 4. Sorting by net_won
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hp_net_won
    ON hand_players (net_won);

-- 5. Batch action fetch by hand_player_id (already partially covered
--    by ix_act_hp_allin, but this index is ordered for range scans)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_hp_street_seq
    ON actions (hand_player_id, street, sequence);

-- 6. Session date lookup (for the session-date dropdown)
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hands_played_date_id
    ON hands (played_date, id);

-- 7. FK index on player_cumulative.hand_id for CASCADE delete speed
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pc_hand_id
    ON player_cumulative (hand_id);

-- 8. Covering index for the slow-path aggregation query (cross-filter).
--    Allows index-only scans over hand_players for stat flag aggregations.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_hp_slowpath_cover
    ON hand_players (player_id)
    INCLUDE (hand_id, position, net_won, bb_won,
             was_vpip, was_pfr, was_3bet, had_3bet_opp,
             was_rfi, had_rfi_opp, saw_flop, went_to_showdown,
             was_cbet, had_cbet_opp, folded_to_3bet, faced_3bet,
             folded_to_btn_steal, faced_btn_steal, was_walk,
             was_4bet, had_4bet_opp, allin_ev_diff,
             postflop_bets_raises, postflop_calls, postflop_checks,
             rake_from_won, rake_attributed)
    WHERE NOT is_sitting_out;

-- 8. FK index on actions.hand_id for CASCADE delete speed
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_act_hand_id
    ON actions (hand_id);
