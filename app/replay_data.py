"""
Data access layer for the hand replayer.

Provides frozen dataclasses and DB fetch helpers to load hand-history
data in a form the replay engine can consume directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session as SASession

from app.models import Hand, Player, HandPlayer, Action, SessionLocal


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SeatPlayer:
    """Static player info for one seat in a hand."""
    seat: int
    name: str
    stack: Decimal          # starting stack (before blinds)
    position: str           # BTN, SB, BB, UTG, MP, CO
    hole_cards: str | None  # e.g. "8d Ks"
    won_amount: Decimal
    showed_hand: bool
    went_to_showdown: bool
    is_hero: bool


@dataclass(frozen=True)
class HandAction:
    """Single action record."""
    street: str             # PREFLOP, FLOP, TURN, RIVER
    sequence: int           # global ordering
    player_name: str
    seat: int
    action_type: str        # fold, check, call, bet, raise
    amount: Decimal
    raise_to: Decimal | None
    is_all_in: bool


@dataclass(frozen=True)
class HandReplayData:
    """All data needed to replay a hand — fully self-contained."""
    hand_id: int            # original hand ID from the site
    db_id: int              # internal database ID
    game_type: str
    small_blind: Decimal
    big_blind: Decimal
    board_cards: tuple[str, ...]  # individual cards, e.g. ("Kh", "Ac", "4s")
    stp_amount: Decimal
    total_pot: Decimal
    rake: Decimal
    button_seat: int
    max_seats: int
    played_at: str
    players: tuple[SeatPlayer, ...]
    actions: tuple[HandAction, ...]
    hero_name: str


# ── Public fetch functions ───────────────────────────────────────────────────

def fetch_hand_for_replay(hand_id: int, hero_name: str = "") -> HandReplayData | None:
    """Fetch a hand by its original site hand_id."""
    db = SessionLocal()
    try:
        hand = db.query(Hand).filter_by(hand_id=hand_id).first()
        return _build_replay_data(db, hand, hero_name) if hand else None
    finally:
        db.close()


def fetch_hand_by_db_id(db_id: int, hero_name: str = "") -> HandReplayData | None:
    """Fetch a hand by its internal database id."""
    db = SessionLocal()
    try:
        hand = db.query(Hand).filter_by(id=db_id).first()
        return _build_replay_data(db, hand, hero_name) if hand else None
    finally:
        db.close()


def fetch_hand_list(
    limit: int = 500,
    offset: int = 0,
    hero_only: bool = True,
    hero_name: str = "",
) -> list[dict]:
    """
    Paginated list of hands for the hand selector.

    Returns list of dicts with keys:
        hand_id, db_id, played_at, stakes, label, net_won
    Ordered by played_at descending (most recent first).
    """
    db = SessionLocal()
    try:
        if hero_only:
            hero = db.query(Player).filter_by(name=hero_name).first()
            if not hero:
                return []

            rows = (
                db.query(HandPlayer, Hand)
                .join(Hand, Hand.id == HandPlayer.hand_id)
                .filter(
                    HandPlayer.player_id == hero.id,
                    HandPlayer.is_sitting_out == False,  # noqa: E712
                )
                .order_by(Hand.played_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
        else:
            hands = (
                db.query(Hand)
                .order_by(Hand.played_at.desc())
                .offset(offset)
                .limit(limit)
                .all()
            )
            # Wrap in (None, hand) pairs for uniform processing
            rows = [(None, h) for h in hands]

        result: list[dict] = []
        for hp, h in rows:
            sb = float(h.small_blind or 0)
            bb = float(h.big_blind or 0)
            net = float(hp.net_won or 0) if hp else 0.0
            net_str = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
            result.append({
                "hand_id": h.hand_id,
                "db_id": h.id,
                "played_at": (
                    h.played_at.strftime("%Y-%m-%d %H:%M") if h.played_at else ""
                ),
                "stakes": f"${sb:.2f}/${bb:.2f}",
                "total_pot": float(h.total_pot or 0),
                "net_won": net,
                "label": (
                    f"#{h.hand_id}  |  "
                    f"{h.played_at.strftime('%m/%d/%Y %H:%M') if h.played_at else '?'}  |  "
                    f"${sb:.2f}/${bb:.2f}  |  "
                    f"{net_str}"
                ),
            })
        return result
    finally:
        db.close()


def fetch_hands_by_ids(hand_ids: list[int], hero_name: str = "") -> list[dict]:
    """
    Fetch hand list entries for a specific set of site hand IDs.

    Used by the replayer when launched from a hands report selection.
    Returns entries in the same format as fetch_hand_list, ordered by
    played_at descending.
    """
    if not hand_ids:
        return []
    db = SessionLocal()
    try:
        hero = db.query(Player).filter_by(name=hero_name).first() if hero_name else None

        q = (
            db.query(HandPlayer, Hand)
            .join(Hand, Hand.id == HandPlayer.hand_id)
            .filter(Hand.hand_id.in_(hand_ids))
        )
        if hero:
            q = q.filter(
                HandPlayer.player_id == hero.id,
                HandPlayer.is_sitting_out == False,  # noqa: E712
            )
        rows = q.order_by(Hand.played_at.desc()).all()

        result: list[dict] = []
        for hp, h in rows:
            sb = float(h.small_blind or 0)
            bb = float(h.big_blind or 0)
            net = float(hp.net_won or 0) if hp else 0.0
            net_str = f"+${net:,.2f}" if net >= 0 else f"-${abs(net):,.2f}"
            result.append({
                "hand_id": h.hand_id,
                "db_id": h.id,
                "played_at": (
                    h.played_at.strftime("%Y-%m-%d %H:%M") if h.played_at else ""
                ),
                "stakes": f"${sb:.2f}/${bb:.2f}",
                "total_pot": float(h.total_pot or 0),
                "net_won": net,
                "label": (
                    f"#{h.hand_id}  |  "
                    f"{h.played_at.strftime('%m/%d/%Y %H:%M') if h.played_at else '?'}  |  "
                    f"${sb:.2f}/${bb:.2f}  |  "
                    f"{net_str}"
                ),
            })
        return result
    finally:
        db.close()


# ── Internal builder ─────────────────────────────────────────────────────────

def _build_replay_data(db: SASession, hand: Hand, hero_name: str = "") -> HandReplayData:
    """Construct a frozen HandReplayData from ORM objects."""

    # Hand players + player names in one join
    hps = (
        db.query(HandPlayer, Player)
        .join(Player, Player.id == HandPlayer.player_id)
        .filter(HandPlayer.hand_id == hand.id)
        .order_by(HandPlayer.seat)
        .all()
    )

    # Lookup: hand_player_id → (player_name, seat)
    hp_lookup: dict[int, tuple[str, int]] = {}
    players: list[SeatPlayer] = []

    for hp, player in hps:
        hp_lookup[hp.id] = (player.name, hp.seat)
        if hp.is_sitting_out:
            continue
        players.append(SeatPlayer(
            seat=hp.seat,
            name=player.name,
            stack=hp.stack or Decimal("0"),
            position=hp.position or "?",
            hole_cards=hp.hole_cards,
            won_amount=hp.won_amount or Decimal("0"),
            showed_hand=hp.showed_hand or False,
            went_to_showdown=hp.went_to_showdown or False,
            is_hero=(player.name == hero_name),
        ))

    # Actions sorted globally by sequence
    db_actions = (
        db.query(Action)
        .filter(Action.hand_id == hand.id)
        .order_by(Action.sequence)
        .all()
    )

    street_order = {"PREFLOP": 0, "FLOP": 1, "TURN": 2, "RIVER": 3}
    actions: list[HandAction] = []
    for act in db_actions:
        name, seat = hp_lookup.get(act.hand_player_id, ("?", 0))
        actions.append(HandAction(
            street=act.street,
            sequence=act.sequence,
            player_name=name,
            seat=seat,
            action_type=act.action_type,
            amount=act.amount or Decimal("0"),
            raise_to=act.raise_to,
            is_all_in=act.is_all_in or False,
        ))

    actions.sort(key=lambda a: (street_order.get(a.street, 99), a.sequence))

    board_cards = tuple(hand.board.split()) if hand.board else ()

    return HandReplayData(
        hand_id=hand.hand_id,
        db_id=hand.id,
        game_type=hand.game_type or "Hold'em No Limit",
        small_blind=hand.small_blind or Decimal("0"),
        big_blind=hand.big_blind or Decimal("0"),
        board_cards=board_cards,
        stp_amount=hand.stp_amount or Decimal("0"),
        total_pot=hand.total_pot or Decimal("0"),
        rake=hand.rake or Decimal("0"),
        button_seat=hand.button_seat or 1,
        max_seats=hand.max_seats or 6,
        played_at=(
            hand.played_at.strftime("%Y-%m-%d %H:%M") if hand.played_at else ""
        ),
        players=tuple(players),
        actions=tuple(actions),
        hero_name=hero_name,
    )
