"""
Hand Replay Engine — deterministic state machine for poker hand replay.

Precomputes a snapshot array at every action boundary so that
``get_state_at(index)`` is O(1).  The engine is fully read-only after
construction and safe to share across Streamlit sessions.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.replay_data import HandReplayData, HandAction

# ── Constants ────────────────────────────────────────────────────────────────

STREET_ORDER = ("PREFLOP", "FLOP", "TURN", "RIVER")
STREET_BOARD_COUNT: dict[str, int] = {
    "PREFLOP": 0,
    "FLOP": 3,
    "TURN": 4,
    "RIVER": 5,
}


# ── State dataclasses ────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    """Mutable per-player state snapshot."""
    seat: int
    name: str
    stack: Decimal
    position: str
    hole_cards: str | None
    is_folded: bool = False
    is_all_in: bool = False
    last_action: str = ""
    current_street_bet: Decimal = field(default_factory=lambda: Decimal("0"))
    total_invested: Decimal = field(default_factory=lambda: Decimal("0"))
    won_amount: Decimal = field(default_factory=lambda: Decimal("0"))
    showed_hand: bool = False
    went_to_showdown: bool = False
    is_hero: bool = False


@dataclass
class GameState:
    """Immutable snapshot of the table at a specific action index."""
    action_index: int           # 0 = initial (blinds posted, cards dealt)
    street: str                 # PREFLOP / FLOP / TURN / RIVER
    board: list[str]            # visible community cards
    pot: Decimal                # collected pot from completed streets
    street_bets: Decimal        # sum of current-street bets
    total_pot: Decimal          # pot + street_bets (display value)
    players: list[PlayerState]
    active_seat: int | None     # seat of player who just acted
    action_text: str            # human-readable description
    is_complete: bool           # True only on the very last state
    hand_id: int
    small_blind: Decimal
    big_blind: Decimal
    button_seat: int
    stp_amount: Decimal
    revealed_seats: frozenset = field(default_factory=frozenset)  # seats force-revealed during showdown


# ── Engine ───────────────────────────────────────────────────────────────────

class HandReplayEngine:
    """
    Deterministic replay engine.

    Precomputes all game states on construction for O(1) random access.

    Usage::

        engine = HandReplayEngine(hand_data)
        state = engine.get_state_at(0)   # deal / blinds posted
        state = engine.get_state_at(5)   # after 5th action
        state = engine.get_state_at(-1)  # final state
    """

    def __init__(self, hand_data: HandReplayData) -> None:
        self._hand = hand_data
        self._states: list[GameState] = []
        self._epilogue_events: list[dict] = []
        self._build_states()

    # ── Public API ───────────────────────────────────────────────────────

    @property
    def total_actions(self) -> int:
        """Number of betting actions (excluding the initial deal state)."""
        return len(self._hand.actions)

    @property
    def max_index(self) -> int:
        """Maximum valid state index."""
        return len(self._states) - 1

    @property
    def hand_data(self) -> HandReplayData:
        return self._hand

    @property
    def epilogue_events(self) -> list[dict]:
        """Events appended after the last betting action: reveals then pot award."""
        return self._epilogue_events

    def get_state_at(self, index: int) -> GameState:
        """Return the game state at *index*.  Negative wraps to end."""
        if index < 0:
            index = self.max_index
        return self._states[min(index, self.max_index)]

    def get_street_indices(self) -> dict[str, int]:
        """Map street name → first state index where that street appears."""
        seen: dict[str, int] = {}
        for s in self._states:
            if s.street not in seen:
                seen[s.street] = s.action_index
        return seen

    # ── State builder ────────────────────────────────────────────────────

    def _build_states(self) -> None:
        hand = self._hand

        # --- Initialise mutable player list ---
        players = [
            PlayerState(
                seat=p.seat,
                name=p.name,
                stack=p.stack,
                position=p.position,
                hole_cards=p.hole_cards,
                is_hero=p.is_hero,
                showed_hand=p.showed_hand,
                went_to_showdown=p.went_to_showdown,
                won_amount=p.won_amount,
            )
            for p in hand.players
        ]
        players.sort(key=lambda p: p.seat)

        # --- Post blinds ---
        for p in players:
            if p.position == "SB":
                p.stack -= hand.small_blind
                p.current_street_bet = hand.small_blind
                p.total_invested = hand.small_blind
                p.last_action = f"SB ${_fmt(hand.small_blind)}"
            elif p.position == "BB":
                p.stack -= hand.big_blind
                p.current_street_bet = hand.big_blind
                p.total_invested = hand.big_blind
                p.last_action = f"BB ${_fmt(hand.big_blind)}"

        # STP is dead money added to the pot
        stp_amount = hand.stp_amount
        collected_pot = Decimal("0")
        current_street = "PREFLOP"

        # State 0 — blinds posted, cards dealt
        self._states.append(self._snapshot(
            index=0,
            street=current_street,
            board=[],
            pot=collected_pot,
            players=players,
            active_seat=None,
            action_text="Hand dealt — blinds posted",
            is_complete=False,
            hand=hand,
        ))

        # State 1 (if applicable) — Splash the Pot added as a synthetic first action
        state_index = 1
        if stp_amount > 0:
            collected_pot = stp_amount
            self._states.append(self._snapshot(
                index=state_index,
                street=current_street,
                board=[],
                pot=collected_pot,
                players=deepcopy(players),  # snapshot players state
                active_seat=None,
                action_text=f"Splash the Pot: ${_fmt(stp_amount)} added to pot",
                is_complete=False,
                hand=hand,
            ))
            state_index = 2

        # --- Walk each action ---
        for i, action in enumerate(hand.actions):
            # Street transition: collect bets into pot, clear per-street state
            if action.street != current_street:
                for p in players:
                    collected_pot += p.current_street_bet
                    p.current_street_bet = Decimal("0")
                    p.last_action = ""
                current_street = action.street

            actor = next((p for p in players if p.seat == action.seat), None)
            if actor is None:
                continue

            text = self._apply_action(actor, action)

            board_count = STREET_BOARD_COUNT.get(current_street, 0)
            visible_board = list(hand.board_cards[:board_count])

            self._states.append(self._snapshot(
                index=state_index + i,
                street=current_street,
                board=visible_board,
                pot=collected_pot,
                players=players,
                active_seat=action.seat,
                action_text=text,
                is_complete=False,
                hand=hand,
            ))

        # --- Epilogue: collect remaining street bets, reveal cards, award pot ---
        for p in players:
            collected_pot += p.current_street_bet
            p.current_street_bet = Decimal("0")
            p.last_action = ""

        board_count = STREET_BOARD_COUNT.get(current_street, 0)
        visible_board = list(hand.board_cards[:board_count])
        next_idx = state_index + len(hand.actions)

        # --- Board run-out for all-in hands: insert a state per missing street ---
        for run_street in STREET_ORDER:
            run_board_count = STREET_BOARD_COUNT[run_street]
            if run_board_count <= board_count:
                continue  # street already dealt
            if len(hand.board_cards) < run_board_count:
                break  # not enough cards recorded (shouldn't happen)
            new_cards = " ".join(hand.board_cards[board_count:run_board_count])
            run_board = list(hand.board_cards[:run_board_count])
            self._epilogue_events.append({
                "type": "runout",
                "index": next_idx,
                "street": run_street,
                "cards": new_cards,
            })
            self._states.append(self._snapshot(
                index=next_idx,
                street=run_street,
                board=run_board,
                pot=collected_pot,
                players=players,
                active_seat=None,
                action_text=f"{run_street}: {new_cards}",
                is_complete=False,
                hand=hand,
                revealed_seats=frozenset(),
            ))
            board_count = run_board_count
            visible_board = run_board
            current_street = run_street
            next_idx += 1

        # Showdown reveals: one state per player who showed cards, in seat order
        showdown_players = sorted(
            [
                p for p in players
                if not p.is_folded
                and (p.went_to_showdown or p.showed_hand)
                and p.hole_cards
            ],
            key=lambda p: p.seat,
        )
        revealed: frozenset = frozenset()
        for sp in showdown_players:
            revealed = revealed | frozenset({sp.seat})
            sp.last_action = f"Shows {sp.hole_cards}"
            self._epilogue_events.append({
                "type": "reveal",
                "index": next_idx,
                "name": sp.name,
                "cards": sp.hole_cards,
            })
            self._states.append(self._snapshot(
                index=next_idx,
                street=current_street,
                board=visible_board,
                pot=collected_pot,
                players=players,
                active_seat=sp.seat,
                action_text=f"{sp.name} shows {sp.hole_cards}",
                is_complete=False,
                hand=hand,
                revealed_seats=revealed,
            ))
            next_idx += 1

        # Pot award: increase winner stacks, pot drops to zero
        for p in players:
            src = next((hp for hp in hand.players if hp.seat == p.seat), None)
            if src and src.won_amount > 0:
                p.stack += src.won_amount
        self._epilogue_events.append({"type": "award", "index": next_idx})
        self._states.append(self._snapshot(
            index=next_idx,
            street=current_street,
            board=visible_board,
            pot=Decimal("0"),
            players=players,
            active_seat=None,
            action_text="Pot awarded",
            is_complete=True,
            hand=hand,
            revealed_seats=revealed,
        ))

    # ── Action application ───────────────────────────────────────────────

    @staticmethod
    def _apply_action(player: PlayerState, action: HandAction) -> str:
        """Mutate *player* state for *action*; return description string."""
        at = action.action_type
        ai_tag = " All-In" if action.is_all_in else ""

        if at == "fold":
            player.is_folded = True
            player.last_action = "Fold"
            return f"{player.name} folds"

        if at == "check":
            player.last_action = "Check"
            return f"{player.name} checks"

        if at == "call":
            amt = action.amount
            player.stack -= amt
            player.current_street_bet += amt
            player.total_invested += amt
            player.is_all_in = player.is_all_in or action.is_all_in
            player.last_action = f"Call ${_fmt(amt)}{ai_tag}"
            return f"{player.name} calls ${_fmt(amt)}{ai_tag}"

        if at == "bet":
            amt = action.amount
            player.stack -= amt
            player.current_street_bet += amt
            player.total_invested += amt
            player.is_all_in = player.is_all_in or action.is_all_in
            player.last_action = f"Bet ${_fmt(amt)}{ai_tag}"
            return f"{player.name} bets ${_fmt(amt)}{ai_tag}"

        if at == "raise":
            if action.raise_to is not None:
                additional = action.raise_to - player.current_street_bet
                player.stack -= additional
                player.total_invested += additional
                player.current_street_bet = action.raise_to
                player.is_all_in = player.is_all_in or action.is_all_in
                player.last_action = f"Raise ${_fmt(action.raise_to)}{ai_tag}"
                return f"{player.name} raises to ${_fmt(action.raise_to)}{ai_tag}"
            else:
                amt = action.amount
                player.stack -= amt
                player.current_street_bet += amt
                player.total_invested += amt
                player.is_all_in = player.is_all_in or action.is_all_in
                player.last_action = f"Raise ${_fmt(amt)}{ai_tag}"
                return f"{player.name} raises ${_fmt(amt)}{ai_tag}"

        # Fallback for unknown action types
        player.last_action = at
        return f"{player.name}: {at}"

    # ── Snapshot helper ──────────────────────────────────────────────────

    @staticmethod
    def _snapshot(
        *,
        index: int,
        street: str,
        board: list[str],
        pot: Decimal,
        players: list[PlayerState],
        active_seat: int | None,
        action_text: str,
        is_complete: bool,
        hand: HandReplayData,
        revealed_seats: frozenset = frozenset(),
    ) -> GameState:
        sb = sum(p.current_street_bet for p in players)
        return GameState(
            action_index=index,
            street=street,
            board=list(board),
            pot=pot,
            street_bets=sb,
            total_pot=pot + sb,
            players=deepcopy(players),
            active_seat=active_seat,
            action_text=action_text,
            is_complete=is_complete,
            hand_id=hand.hand_id,
            small_blind=hand.small_blind,
            big_blind=hand.big_blind,
            button_seat=hand.button_seat,
            stp_amount=hand.stp_amount,
            revealed_seats=revealed_seats,
        )


# ── Formatting helper ────────────────────────────────────────────────────────

def _fmt(val: Decimal) -> str:
    """Format Decimal for display — drop trailing zeros."""
    f = float(val)
    if f == int(f):
        return f"{int(f):,}"
    return f"{f:,.2f}"
