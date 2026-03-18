"""SQLAlchemy ORM models – PostgreSQL schema for the poker tracker."""

from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Numeric,
    DateTime,
    Date,
    Boolean,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, deferred

from app.config import DATABASE_URL

Base = declarative_base()


# ── Hand ─────────────────────────────────────────────────────────────────────
class Hand(Base):
    """One poker hand (deal)."""

    __tablename__ = "hands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hand_id = Column(BigInteger, nullable=False, unique=True, index=True)
    game_type = Column(String(64), nullable=False, default="Hold'em No Limit")
    small_blind = Column(Numeric(10, 2), nullable=False)
    big_blind = Column(Numeric(10, 2), nullable=False)
    table_id = Column(String(32))
    max_seats = Column(Integer)
    button_seat = Column(Integer)
    played_at = Column(DateTime, nullable=False, index=True)
    played_date = Column(Date, nullable=False, index=True)

    # Board cards (space-separated, e.g. "Kh Ac 4s 2h As")
    board = Column(String(32))

    # Pot information
    total_pot = Column(Numeric(10, 2))
    main_pot = Column(Numeric(10, 2))
    stp_amount = Column(Numeric(10, 2), default=0)  # Splash the Pot dead money
    rake = Column(Numeric(10, 2), default=0)

    # Raw text for replayer (deferred — only loaded when accessed)
    raw_text = deferred(Column(Text))

    # Relationships
    players = relationship("HandPlayer", back_populates="hand", cascade="all, delete-orphan")
    actions = relationship("Action", back_populates="hand", cascade="all, delete-orphan")


# ── Player (global) ──────────────────────────────────────────────────────────
class Player(Base):
    """Distinct player seen across all hands."""

    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, unique=True, index=True)

    hand_players = relationship("HandPlayer", back_populates="player")


# ── HandPlayer (per-hand player info) ────────────────────────────────────────
class HandPlayer(Base):
    """Player's participation in a specific hand."""

    __tablename__ = "hand_players"
    __table_args__ = (
        UniqueConstraint("hand_id", "player_id", name="uq_hand_player"),
        Index("ix_hp_player_active", "player_id", "is_sitting_out"),
        Index("ix_hp_hand_showdown", "hand_id", "went_to_showdown"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    hand_id = Column(Integer, ForeignKey("hands.id", ondelete="CASCADE"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)

    seat = Column(Integer)
    stack = Column(Numeric(10, 2))
    position = Column(String(16))  # SB, BB, UTG, MP, CO, BTN, etc.
    is_sitting_out = Column(Boolean, default=False)

    # Hole cards (e.g. "8d Ks" or "Tc Qd Th 4c" for Omaha)
    hole_cards = Column(String(16))

    # Result
    won_amount = Column(Numeric(10, 2), default=0)
    net_won = Column(Numeric(10, 2), default=0)  # won − total invested
    total_invested = Column(Numeric(10, 2), default=0)

    # Summary flags
    showed_hand = Column(Boolean, default=False)
    went_to_showdown = Column(Boolean, default=False)

    # ── Precomputed stat flags (set at import time) ──────────────────────────
    was_vpip = Column(Boolean, default=False)
    was_pfr = Column(Boolean, default=False)
    was_3bet = Column(Boolean, default=False)
    had_3bet_opp = Column(Boolean, default=False)
    was_4bet = Column(Boolean, default=False)
    had_4bet_opp = Column(Boolean, default=False)
    was_rfi = Column(Boolean, default=False)
    had_rfi_opp = Column(Boolean, default=False)
    folded_to_3bet = Column(Boolean, default=False)
    faced_3bet = Column(Boolean, default=False)
    saw_flop = Column(Boolean, default=False)
    was_cbet = Column(Boolean, default=False)
    had_cbet_opp = Column(Boolean, default=False)
    folded_to_btn_steal = Column(Boolean, default=False)
    faced_btn_steal = Column(Boolean, default=False)
    was_walk = Column(Boolean, default=False)
    hero_was_allin = Column(Boolean, default=False)
    allin_ev_diff = Column(Numeric(10, 2), default=0)
    postflop_bets_raises = Column(Integer, default=0)
    postflop_calls = Column(Integer, default=0)
    postflop_checks = Column(Integer, default=0)
    rake_attributed = Column(Numeric(10, 2), default=0)
    rake_from_won = Column(Numeric(10, 2), default=0)
    bb_won = Column(Numeric(12, 4), default=0)

    # Relationships
    hand = relationship("Hand", back_populates="players")
    player = relationship("Player", back_populates="hand_players")
    actions = relationship("Action", back_populates="hand_player", cascade="all, delete-orphan")


# ── Action ───────────────────────────────────────────────────────────────────
class Action(Base):
    """Individual betting action within a hand."""

    __tablename__ = "actions"
    __table_args__ = (
        Index("ix_act_hp_allin", "hand_player_id", "is_all_in"),
        Index("ix_act_hand_street_seq", "hand_id", "street", "sequence"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    hand_id = Column(Integer, ForeignKey("hands.id", ondelete="CASCADE"), nullable=False)
    hand_player_id = Column(Integer, ForeignKey("hand_players.id", ondelete="CASCADE"), nullable=False)

    street = Column(String(16), nullable=False)  # PREFLOP, FLOP, TURN, RIVER
    sequence = Column(Integer, nullable=False)     # ordering within the street
    action_type = Column(String(16), nullable=False)  # fold, call, raise, check, bet, all-in
    amount = Column(Numeric(10, 2), default=0)
    raise_to = Column(Numeric(10, 2))              # "raises $X to $Y" → this stores Y
    is_all_in = Column(Boolean, default=False)

    # Relationships
    hand = relationship("Hand", back_populates="actions")
    hand_player = relationship("HandPlayer", back_populates="actions")


# ── Session (derived, optional grouping) ─────────────────────────────────────
class Session(Base):
    """Logical session grouping (derived after import)."""

    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    session_date = Column(Date, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime)
    total_hands = Column(Integer, default=0)
    net_won = Column(Numeric(10, 2), default=0)
    rake_paid = Column(Numeric(10, 2), default=0)
    rake_attributed = Column(Numeric(10, 2), default=0)


# ── Player Stat Summary (precomputed aggregates) ─────────────────────────────
class PlayerStatSummary(Base):
    """Precomputed stat aggregates by various groupings, updated at import time."""

    __tablename__ = "player_stat_summaries"
    __table_args__ = (
        UniqueConstraint("player_id", "grouping_type", "group_key",
                         name="uq_pss_player_type_key"),
        Index("ix_pss_lookup", "player_id", "grouping_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    grouping_type = Column(String(16), nullable=False)
    group_key = Column(String(64), nullable=False)

    total_hands = Column(Integer, default=0)
    walk_count = Column(Integer, default=0)
    vpip_count = Column(Integer, default=0)
    pfr_count = Column(Integer, default=0)
    rfi_count = Column(Integer, default=0)
    rfi_opportunities = Column(Integer, default=0)
    three_bet_count = Column(Integer, default=0)
    three_bet_opportunities = Column(Integer, default=0)
    four_bet_count = Column(Integer, default=0)
    four_bet_opportunities = Column(Integer, default=0)
    fold_to_3bet_count = Column(Integer, default=0)
    fold_to_3bet_opportunities = Column(Integer, default=0)
    saw_flop_count = Column(Integer, default=0)
    went_to_sd_count = Column(Integer, default=0)
    went_to_sd_from_flop_count = Column(Integer, default=0)
    won_at_sd_count = Column(Integer, default=0)
    won_when_saw_flop_count = Column(Integer, default=0)
    cbet_count = Column(Integer, default=0)
    cbet_opportunities = Column(Integer, default=0)
    fold_to_btn_steal_count = Column(Integer, default=0)
    fold_to_btn_steal_opportunities = Column(Integer, default=0)
    postflop_bets_raises = Column(Integer, default=0)
    postflop_calls = Column(Integer, default=0)
    postflop_checks = Column(Integer, default=0)
    net_won = Column(Numeric(12, 2), default=0)
    sd_won = Column(Numeric(12, 2), default=0)
    nonsd_won = Column(Numeric(12, 2), default=0)
    bb_won_total = Column(Numeric(12, 4), default=0)
    allin_ev_diff = Column(Numeric(12, 2), default=0)
    rake_from_won = Column(Numeric(12, 2), default=0)
    rake_attributed = Column(Numeric(12, 2), default=0)


# ── Player Cumulative P&L ────────────────────────────────────────────────────
class PlayerCumulative(Base):
    """Per-hand cumulative P&L for the chart, populated at import time."""

    __tablename__ = "player_cumulative"
    __table_args__ = (
        Index("ix_pc_player_hand", "player_id", "hand_number"),
        Index("ix_pc_player_played_at", "player_id", "played_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    hand_number = Column(Integer, nullable=False)
    hand_id = Column(Integer, ForeignKey("hands.id", ondelete="CASCADE"), nullable=False)
    played_at = Column(DateTime, nullable=False)
    net_won_cumulative = Column(Numeric(12, 2), default=0)
    sd_won_cumulative = Column(Numeric(12, 2), default=0)
    nonsd_won_cumulative = Column(Numeric(12, 2), default=0)
    allin_ev_cumulative = Column(Numeric(12, 2), default=0)


# ── Engine / Session factory ─────────────────────────────────────────────────
_engine = None
_SessionLocal = None


def _get_engine():
    """Lazy-initialise the SQLAlchemy engine on first use."""
    global _engine
    if _engine is None:
        _engine = create_engine(
            DATABASE_URL, echo=False, pool_pre_ping=True,
        )
    return _engine


def get_session_factory():
    """Return (and cache) the sessionmaker bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=_get_engine())
    return _SessionLocal


# Public aliases — drop-in replacements for existing usage.
class _EngineProxy:
    """Proxy so ``models.engine`` still works without module-level init."""

    def __getattr__(self, name):
        return getattr(_get_engine(), name)


engine = _EngineProxy()


class _SessionLocalProxy:
    """Proxy so ``SessionLocal()`` still works."""

    def __call__(self, *a, **kw):
        return get_session_factory()(*a, **kw)

    def __getattr__(self, name):
        return getattr(get_session_factory(), name)


SessionLocal = _SessionLocalProxy()


def init_db():
    """Create all tables if they don't exist."""
    Base.metadata.create_all(_get_engine())
