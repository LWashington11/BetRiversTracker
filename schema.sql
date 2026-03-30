-- PostgreSQL schema for BetRivers Poker Tracker - Unofficial
-- Run this manually or let SQLAlchemy create the tables via `python -m app.cli init`

CREATE DATABASE betrivers_tracker;

\c betrivers_tracker;

-- Hands
CREATE TABLE IF NOT EXISTS hands (
    id              SERIAL PRIMARY KEY,
    hand_id         BIGINT NOT NULL UNIQUE,
    game_type       VARCHAR(64) NOT NULL DEFAULT 'Hold''em No Limit',
    small_blind     NUMERIC(10,2) NOT NULL,
    big_blind       NUMERIC(10,2) NOT NULL,
    table_id        VARCHAR(32),
    max_seats       INTEGER,
    button_seat     INTEGER,
    played_at       TIMESTAMP NOT NULL,
    played_date     DATE NOT NULL,
    board           VARCHAR(32),
    total_pot       NUMERIC(10,2),
    main_pot        NUMERIC(10,2),
    stp_amount      NUMERIC(10,2) DEFAULT 0,
    rake            NUMERIC(10,2) DEFAULT 0,
    raw_text        TEXT
);

CREATE INDEX IF NOT EXISTS idx_hands_hand_id ON hands(hand_id);
CREATE INDEX IF NOT EXISTS idx_hands_played_at ON hands(played_at);
CREATE INDEX IF NOT EXISTS idx_hands_played_date ON hands(played_date);

-- Players
CREATE TABLE IF NOT EXISTS players (
    id   SERIAL PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);

-- Hand-Player (participation)
CREATE TABLE IF NOT EXISTS hand_players (
    id              SERIAL PRIMARY KEY,
    hand_id         INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    seat            INTEGER,
    stack           NUMERIC(10,2),
    position        VARCHAR(16),
    is_sitting_out  BOOLEAN DEFAULT FALSE,
    hole_cards      VARCHAR(16),
    won_amount      NUMERIC(10,2) DEFAULT 0,
    net_won         NUMERIC(10,2) DEFAULT 0,
    total_invested  NUMERIC(10,2) DEFAULT 0,
    showed_hand     BOOLEAN DEFAULT FALSE,
    went_to_showdown BOOLEAN DEFAULT FALSE,

    -- Precomputed stat flags (set at import time)
    was_vpip            BOOLEAN DEFAULT FALSE,
    was_pfr             BOOLEAN DEFAULT FALSE,
    was_3bet            BOOLEAN DEFAULT FALSE,
    had_3bet_opp        BOOLEAN DEFAULT FALSE,
    was_4bet            BOOLEAN DEFAULT FALSE,
    had_4bet_opp        BOOLEAN DEFAULT FALSE,
    was_rfi             BOOLEAN DEFAULT FALSE,
    had_rfi_opp         BOOLEAN DEFAULT FALSE,
    folded_to_3bet      BOOLEAN DEFAULT FALSE,
    faced_3bet          BOOLEAN DEFAULT FALSE,
    saw_flop            BOOLEAN DEFAULT FALSE,
    was_cbet            BOOLEAN DEFAULT FALSE,
    had_cbet_opp        BOOLEAN DEFAULT FALSE,
    folded_to_btn_steal BOOLEAN DEFAULT FALSE,
    faced_btn_steal     BOOLEAN DEFAULT FALSE,
    was_walk            BOOLEAN DEFAULT FALSE,
    hero_was_allin      BOOLEAN DEFAULT FALSE,
    allin_ev_diff       NUMERIC(10,2) DEFAULT 0,
    postflop_bets_raises INTEGER DEFAULT 0,
    postflop_calls      INTEGER DEFAULT 0,
    postflop_checks     INTEGER DEFAULT 0,
    rake_attributed     NUMERIC(10,2) DEFAULT 0,
    rake_from_won       NUMERIC(10,2) DEFAULT 0,
    bb_won              NUMERIC(12,4) DEFAULT 0,

    UNIQUE(hand_id, player_id)
);

-- Actions
CREATE TABLE IF NOT EXISTS actions (
    id              SERIAL PRIMARY KEY,
    hand_id         INTEGER NOT NULL REFERENCES hands(id) ON DELETE CASCADE,
    hand_player_id  INTEGER NOT NULL REFERENCES hand_players(id) ON DELETE CASCADE,
    street          VARCHAR(16) NOT NULL,
    sequence        INTEGER NOT NULL,
    action_type     VARCHAR(16) NOT NULL,
    amount          NUMERIC(10,2) DEFAULT 0,
    raise_to        NUMERIC(10,2),
    is_all_in       BOOLEAN DEFAULT FALSE
);

-- Performance indexes
CREATE INDEX IF NOT EXISTS ix_hp_player_active ON hand_players(player_id, is_sitting_out);
CREATE INDEX IF NOT EXISTS ix_hp_hand_showdown ON hand_players(hand_id, went_to_showdown);
CREATE INDEX IF NOT EXISTS ix_act_hp_allin ON actions(hand_player_id, is_all_in);
CREATE INDEX IF NOT EXISTS ix_act_hand_street_seq ON actions(hand_id, street, sequence);

-- Sessions (derived)
CREATE TABLE IF NOT EXISTS sessions (
    id              SERIAL PRIMARY KEY,
    player_id       INTEGER NOT NULL REFERENCES players(id),
    session_date    DATE NOT NULL,
    start_time      TIMESTAMP NOT NULL,
    end_time        TIMESTAMP,
    total_hands     INTEGER DEFAULT 0,
    net_won         NUMERIC(10,2) DEFAULT 0,
    rake_paid       NUMERIC(10,2) DEFAULT 0,
    rake_attributed NUMERIC(10,2) DEFAULT 0
);

-- Player stat summary (precomputed aggregates, updated at import time)
CREATE TABLE IF NOT EXISTS player_stat_summaries (
    id                  SERIAL PRIMARY KEY,
    player_id           INTEGER NOT NULL REFERENCES players(id),
    grouping_type       VARCHAR(16) NOT NULL,   -- 'total', 'date', 'stakes', 'position'
    group_key           VARCHAR(64) NOT NULL,   -- e.g. '2025-09-29', '$0.50/$1.00 NL Holdem|6 Max', 'BTN'
    total_hands         INTEGER DEFAULT 0,
    walk_count          INTEGER DEFAULT 0,
    vpip_count          INTEGER DEFAULT 0,
    pfr_count           INTEGER DEFAULT 0,
    rfi_count           INTEGER DEFAULT 0,
    rfi_opportunities   INTEGER DEFAULT 0,
    three_bet_count     INTEGER DEFAULT 0,
    three_bet_opportunities INTEGER DEFAULT 0,
    four_bet_count      INTEGER DEFAULT 0,
    four_bet_opportunities INTEGER DEFAULT 0,
    fold_to_3bet_count  INTEGER DEFAULT 0,
    fold_to_3bet_opportunities INTEGER DEFAULT 0,
    saw_flop_count      INTEGER DEFAULT 0,
    went_to_sd_count    INTEGER DEFAULT 0,
    went_to_sd_from_flop_count INTEGER DEFAULT 0,
    won_at_sd_count     INTEGER DEFAULT 0,
    won_when_saw_flop_count INTEGER DEFAULT 0,
    cbet_count          INTEGER DEFAULT 0,
    cbet_opportunities  INTEGER DEFAULT 0,
    fold_to_btn_steal_count INTEGER DEFAULT 0,
    fold_to_btn_steal_opportunities INTEGER DEFAULT 0,
    postflop_bets_raises INTEGER DEFAULT 0,
    postflop_calls      INTEGER DEFAULT 0,
    postflop_checks     INTEGER DEFAULT 0,
    net_won             NUMERIC(12,2) DEFAULT 0,
    sd_won              NUMERIC(12,2) DEFAULT 0,
    nonsd_won           NUMERIC(12,2) DEFAULT 0,
    bb_won_total        NUMERIC(12,4) DEFAULT 0,
    allin_ev_diff       NUMERIC(12,2) DEFAULT 0,
    rake_from_won       NUMERIC(12,2) DEFAULT 0,
    rake_attributed     NUMERIC(12,2) DEFAULT 0,
    UNIQUE(player_id, grouping_type, group_key)
);

CREATE INDEX IF NOT EXISTS ix_pss_lookup
    ON player_stat_summaries(player_id, grouping_type);

-- Player cumulative P&L (one row per hand per player, for P&L chart)
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
);

CREATE INDEX IF NOT EXISTS ix_pc_player_hand
    ON player_cumulative(player_id, hand_number);

-- Additional performance indexes for the new aggregation architecture

-- bb_won filter push-down (hands report "min/max bb" filter)
CREATE INDEX IF NOT EXISTS ix_hp_player_bbwon
    ON hand_players(player_id, bb_won)
    WHERE NOT is_sitting_out;

-- Cumulative P&L date-range filter
CREATE INDEX IF NOT EXISTS ix_pc_player_played_at
    ON player_cumulative(player_id, played_at);

-- FK indexes for CASCADE delete performance
CREATE INDEX IF NOT EXISTS ix_pc_hand_id
    ON player_cumulative(hand_id);
CREATE INDEX IF NOT EXISTS ix_act_hand_id
    ON actions(hand_id);
