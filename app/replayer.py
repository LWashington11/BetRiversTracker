"""
BetRivers Poker Tracker - Unofficial — Hand Replayer

Standalone Streamlit page that renders an interactive hand-history
replayer with an oval poker-table layout, step-through controls,
visibility toggles, action log, and jump-to-street buttons.

Launch with::

    streamlit run app/replayer.py
"""

from __future__ import annotations

import html as html_mod
import sys
import time
from decimal import Decimal
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import streamlit as st
import streamlit.components.v1 as components

from app.replay_data import (
    fetch_hand_for_replay,
    fetch_hand_list,
    fetch_hands_by_ids,
    HandReplayData,
)
from app.replay_engine import HandReplayEngine, GameState, PlayerState, STREET_BOARD_COUNT
from app.constants import REPLAYER_LIST_LIMIT


# ═════════════════════════════════════════════════════════════════════════════
# Session-state defaults
# ═════════════════════════════════════════════════════════════════════════════

_DEFAULTS: dict = {
    "action_index": 0,
    "show_hero_cards": True,
    "show_results": False,
    "hand_list_idx": 0,
    "_prev_hand_id": None,
    "is_playing": False,
    "play_speed": 1.5,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)


# ═════════════════════════════════════════════════════════════════════════════
# Cached data
# ═════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=120, show_spinner="Loading hand list…")
def _cached_hand_list(hero_name: str) -> list[dict]:
    """Fetch hand list for the replayer selector."""
    return fetch_hand_list(limit=REPLAYER_LIST_LIMIT, hero_name=hero_name)


@st.cache_resource(show_spinner="Building replay engine…")
def _cached_engine(hand_id: int, hero_name: str) -> HandReplayEngine | None:
    """Build a replay engine for the given hand."""
    data = fetch_hand_for_replay(hand_id, hero_name=hero_name)
    if data is None:
        return None
    return HandReplayEngine(data)


# ═════════════════════════════════════════════════════════════════════════════
# Build hand list — either from Hands Report selection or full list
# ═════════════════════════════════════════════════════════════════════════════

_hero = st.session_state.get("hero_name", "")

# Clear a pre-filtered hand selection only when navigating to this page
# fresh (not via hands report) — never on an in-page rerun (button clicks,
# auto-play, etc.) so the selection survives ⏮/⏭ and other controls.
if st.session_state.get("_from_hands_report", False):
    # Arriving from Hands Report: keep replayer_hand_ids, consume the flag.
    st.session_state["_from_hands_report"] = False
elif st.session_state.get("_prev_hand_id") is None:
    # Fresh page load (no hand has been displayed yet): reset to full list.
    st.session_state["replayer_hand_ids"] = None

# Check if we were launched from Hands Report with a selected subset
_replayer_ids = st.session_state.get("replayer_hand_ids", None)

full_hand_list = _cached_hand_list(_hero)
if not full_hand_list:
    st.warning("No hands in the database. Import hand histories first.")
    st.stop()

if _replayer_ids:
    # Fetch the selected hands directly by their site IDs — avoids the
    # 500-hand cap on the cached list, which can exclude older sessions.
    hand_list = fetch_hands_by_ids(_replayer_ids, hero_name=_hero)
    if not hand_list:
        hand_list = full_hand_list
        _replayer_ids = None
    else:
        # Reset index to first selected hand if arriving fresh
        if st.session_state.get("_prev_hand_id") is None:
            st.session_state.hand_list_idx = 0
else:
    hand_list = full_hand_list

hand_ids = [h["hand_id"] for h in hand_list]
hand_labels = {h["hand_id"]: h["label"] for h in hand_list}

# ═════════════════════════════════════════════════════════════════════════════
# Top bar: hand selector + navigation + toggles (inline, not sidebar)
# ═════════════════════════════════════════════════════════════════════════════

# Header row
hdr_left, hdr_right = st.columns([7, 3])
with hdr_left:
    st.markdown("### 🃏 Hand Replayer")
with hdr_right:
    if _replayer_ids:
        st.caption(f"Viewing {len(hand_list)} selected hand(s)")

# Hand selector — Row 1: search + hand selectbox
sel_c1, sel_c2 = st.columns([2, 4], vertical_alignment="bottom")

with sel_c1:
    search_id = st.number_input(
        "Jump to Hand #", value=0, min_value=0, step=1, format="%d",
        help="Enter a hand ID to jump directly to that hand",
        key="rep_search_id",
    )

if search_id > 0 and search_id in hand_labels:
    default_idx = hand_ids.index(search_id)
elif st.session_state.hand_list_idx < len(hand_ids):
    default_idx = st.session_state.hand_list_idx
else:
    default_idx = 0

with sel_c2:
    selected_hand_id = st.selectbox(
        "Hand",
        options=hand_ids,
        index=default_idx,
        format_func=lambda x: hand_labels[x],
        label_visibility="collapsed",
    )

# Detect hand change → reset action index
if selected_hand_id != st.session_state.get("_prev_hand_id"):
    st.session_state.action_index = 0
    st.session_state._prev_hand_id = selected_hand_id
    st.session_state.hand_list_idx = (
        hand_ids.index(selected_hand_id) if selected_hand_id in hand_ids else 0
    )
    st.session_state.is_playing = False  # stop auto-play on manual hand change

cur_list_idx = (
    hand_ids.index(selected_hand_id) if selected_hand_id in hand_ids else 0
)

# Hand selector — Row 2: nav buttons + toggles
nav_c1, nav_c2, nav_c3, nav_c4 = st.columns(4, gap="small")

with nav_c1:
    if st.button("← Prev", disabled=(cur_list_idx >= len(hand_ids) - 1),
                  use_container_width=True):
        st.session_state.hand_list_idx = cur_list_idx + 1
        st.session_state.action_index = 0
        st.session_state._prev_hand_id = hand_ids[cur_list_idx + 1]
        st.rerun()
with nav_c2:
    if st.button("Next →", disabled=(cur_list_idx <= 0),
                  use_container_width=True):
        st.session_state.hand_list_idx = cur_list_idx - 1
        st.session_state.action_index = 0
        st.session_state._prev_hand_id = hand_ids[cur_list_idx - 1]
        st.rerun()
with nav_c3:
    show_hero = st.toggle("Hero Cards", value=st.session_state.show_hero_cards)
    st.session_state.show_hero_cards = show_hero
with nav_c4:
    show_results = st.toggle("Results", value=st.session_state.show_results)
    st.session_state.show_results = show_results

# Net result display
sel_meta = next((h for h in hand_list if h["hand_id"] == selected_hand_id), None)


# ═════════════════════════════════════════════════════════════════════════════
# Load engine & current state
# ═════════════════════════════════════════════════════════════════════════════

engine = _cached_engine(selected_hand_id, _hero)
if engine is None:
    st.error(f"Hand #{selected_hand_id} not found in database.")
    st.stop()

idx = st.session_state.action_index
state = engine.get_state_at(idx)


# ═════════════════════════════════════════════════════════════════════════════
# Header
# ═════════════════════════════════════════════════════════════════════════════

hd = engine.hand_data

# Compact info bar
info_c1, info_c2, info_c3 = st.columns([4, 4, 2])
with info_c1:
    st.caption(
        f"**Hand #{state.hand_id}**  ·  {hd.game_type}  ·  "
        f"${hd.small_blind}/{hd.big_blind}  ·  {hd.max_seats}-Max"
    )
with info_c2:
    st.caption(f"{hd.played_at}")
with info_c3:
    if sel_meta:
        net = sel_meta["net_won"]
        color = "#2ecc71" if net >= 0 else "#e74c3c"
        sign = "+" if net >= 0 else ""
        st.markdown(
            f'<span style="color:{color};font-weight:bold;font-size:16px;">'
            f'{sign}${net:,.2f}</span>',
            unsafe_allow_html=True,
        )


# ═════════════════════════════════════════════════════════════════════════════
# Poker table rendering (HTML/CSS)
# ═════════════════════════════════════════════════════════════════════════════

# -- Seat layout coordinates: (left%, top%) --
# Position 0 = hero (bottom centre).  Clockwise from there.

_SEAT_COORDS: dict[int, list[tuple[float, float]]] = {
    # Hero is always index 0 (bottom centre). Remaining positions go clockwise:
    # bottom → left-bottom → left-top → top → right-top → right-bottom …
    # Top-row Y values kept ≥ 14% so position badges don't clip inside the iframe.
    2: [(50, 86), (50, 14)],
    3: [(50, 86), (18, 22), (82, 22)],
    4: [(50, 86), (15, 46), (50, 14), (85, 46)],
    5: [(50, 86), (16, 60), (22, 14), (78, 14), (84, 60)],
    6: [(50, 86), (15, 60), (15, 18), (50, 12), (85, 18), (85, 60)],
    7: [(50, 88), (16, 66), (12, 30), (36, 12), (64, 12), (88, 30), (84, 66)],
    8: [(50, 88), (18, 72), (10, 42), (24, 14), (50, 12), (76, 14), (90, 42), (82, 72)],
    9: [(50, 88), (22, 76), (10, 50), (14, 18), (36, 12), (64, 12), (86, 18), (90, 50), (78, 76)],
}

_TABLE_CENTER = (50, 42)

# Card suit rendering
_SUIT_HTML = {"h": "♥", "d": "♦", "c": "♣", "s": "♠"}
_SUIT_COLOR = {"h": "#e74c3c", "d": "#2980b9", "c": "#27ae60", "s": "#2c3e50"}


def _esc(text: str) -> str:
    return html_mod.escape(text)


def _fmt_f(val: float) -> str:
    if val == int(val) and abs(val) < 1_000_000:
        return f"{int(val):,}"
    return f"{val:,.2f}"


def _card_html(card: str, small: bool = False) -> str:
    """Render one face-up card."""
    rank = card[:-1]
    suit = card[-1].lower()
    sym = _SUIT_HTML.get(suit, "?")
    color = _SUIT_COLOR.get(suit, "#000")
    cls = "card card-sm" if small else "card"
    return (
        f'<div class="{cls}" style="color:{color};">'
        f'<span class="rank">{rank}</span>'
        f'<span class="suit">{sym}</span>'
        f'</div>'
    )


def _card_back_html(small: bool = False) -> str:
    cls = "card card-back card-sm" if small else "card card-back"
    return f'<div class="{cls}"></div>'


def _should_show_cards(
    player: PlayerState,
    show_hero: bool,
    show_results: bool,
    revealed_seats: frozenset = frozenset(),
) -> bool:
    """Determine whether to reveal a player's hole cards."""
    if player.seat in revealed_seats:
        return True
    if player.is_hero and show_hero:
        return True
    if show_results and (player.showed_hand or player.went_to_showdown):
        return True
    return False


def _bet_pos(seat: tuple[float, float]) -> tuple[float, float]:
    """Position for a bet chip — 42 % of the way from seat to table centre."""
    cx, cy = _TABLE_CENTER
    return (seat[0] + (cx - seat[0]) * 0.42, seat[1] + (cy - seat[1]) * 0.42)


def render_table_html(
    state: GameState,
    show_hero: bool,
    show_results: bool,
) -> str:
    """Return a self-contained HTML document visualising the poker table."""

    players = sorted(state.players, key=lambda p: p.seat)
    n = len(players)
    if n == 0:
        return "<p>No players</p>"

    coords = _SEAT_COORDS.get(n, _SEAT_COORDS.get(min(n, 9), _SEAT_COORDS[6]))

    # Rotate so hero is at visual position 0 (bottom centre)
    hero_idx = next((i for i, p in enumerate(players) if p.is_hero), 0)
    seat_pos: dict[int, tuple[float, float]] = {}
    for i, p in enumerate(players):
        vi = (i - hero_idx) % n
        seat_pos[p.seat] = coords[vi]

    # ── Player HTML ──────────────────────────────────────────────────────
    players_html = ""
    bets_html = ""

    for p in players:
        pos = seat_pos[p.seat]

        # Card visibility
        show = _should_show_cards(
            p, show_hero, show_results,
            getattr(state, "revealed_seats", frozenset()),
        )
        card_strs = p.hole_cards.split() if p.hole_cards else []
        cards_h = ""
        if not p.is_folded:
            if show and card_strs:
                # Reveal known cards face-up
                cards_h = "".join(_card_html(c, small=True) for c in card_strs)
            elif not p.is_hero:
                # Always show 2 face-down cards for non-folded opponents,
                # even when their hole cards are unknown from the hand history.
                n_cards = len(card_strs) if card_strs else 2
                cards_h = "".join(_card_back_html(small=True) for _ in range(n_cards))
            elif card_strs:
                # Hero with cards when show_hero is toggled off
                cards_h = "".join(_card_back_html(small=True) for _ in card_strs)
        elif show and card_strs:
            # Folded player whose cards are known — show dimmed face-up
            # (dimming handled by .folded CSS opacity on the player-box)
            cards_h = "".join(_card_html(c, small=True) for c in card_strs)
        # Folded players with unknown cards: cards_h stays "" (nothing shown)

        # CSS classes
        cls = ["player-box"]
        if p.is_hero:
            cls.append("hero")
        if p.seat == state.active_seat:
            cls.append("active")
        if p.is_folded:
            cls.append("folded")

        action_h = (
            f'<div class="p-action">{_esc(p.last_action)}</div>'
            if p.last_action else ""
        )
        won_h = ""
        if state.is_complete and float(p.won_amount) > 0:
            won_h = f'<div class="p-won">+${_fmt_f(float(p.won_amount))}</div>'

        dealer_h = ""
        if p.seat == state.button_seat:
            # Dealer button placement offset
            dealer_h = '<div class="dealer-btn">D</div>'

        players_html += f'''
        <div class="seat" style="left:{pos[0]}%;top:{pos[1]}%;">
            {dealer_h}
            <div class="{' '.join(cls)}">
                <div class="p-pos">{_esc(p.position)}</div>
                <div class="p-name">{_esc(p.name)}</div>
                <div class="p-stack">${_fmt_f(float(p.stack))}</div>
                {action_h}
                {won_h}
            </div>
            <div class="hole-cards">{cards_h}</div>
        </div>'''

        # Bet chip
        if float(p.current_street_bet) > 0 and not p.is_folded:
            bp = _bet_pos(pos)
            bets_html += (
                f'<div class="bet-chip" style="left:{bp[0]}%;top:{bp[1]}%;">'
                f'${_fmt_f(float(p.current_street_bet))}</div>'
            )

    # ── Community cards ──────────────────────────────────────────────────
    comm_h = "".join(_card_html(c) for c in state.board)

    # ── Pot ───────────────────────────────────────────────────────────────
    pot_val = float(state.total_pot)
    stp_note = ""
    if float(state.stp_amount) > 0:
        stp_note = f'<div class="stp-note">Includes ${_fmt_f(float(state.stp_amount))} STP</div>'

    # ── Street label ─────────────────────────────────────────────────────
    street_label = state.street

    return _HTML_TEMPLATE.format(
        css=_TABLE_CSS,
        players=players_html,
        bets=bets_html,
        community=comm_h,
        pot=_fmt_f(pot_val),
        stp_note=stp_note,
        street=street_label,
    )


# ═════════════════════════════════════════════════════════════════════════════
# CSS — poker table styling
# ═════════════════════════════════════════════════════════════════════════════

_TABLE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    background: transparent;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    overflow: visible;
}
.poker-container {
    position: relative;
    width: 100%;
    max-width: 920px;
    height: 500px;
    margin: 0 auto;
    overflow: visible;
}

/* ── Green felt oval ──────────────────────────────────────────────────── */
.table-felt {
    position: absolute;
    left: 50%; top: 48%;
    transform: translate(-50%, -50%);
    width: 62%; height: 58%;
    background: radial-gradient(ellipse, #1a6b37 0%, #145a2d 55%, #0d4820 100%);
    border-radius: 50%;
    border: 10px solid #8B6914;
    box-shadow:
        0 0 0 4px #6b5210,
        0 0 40px rgba(0,0,0,0.55),
        inset 0 0 40px rgba(0,0,0,0.30);
}

/* ── Pot display ──────────────────────────────────────────────────────── */
.pot-area {
    position: absolute;
    top: 22%; left: 50%;
    transform: translateX(-50%);
    text-align: center;
}
.pot-display {
    color: #FFD700;
    font-size: 15px;
    font-weight: 700;
    text-shadow: 1px 1px 3px rgba(0,0,0,0.8);
    padding: 3px 12px;
    background: rgba(0,0,0,0.35);
    border-radius: 12px;
}
.stp-note {
    color: #aaa;
    font-size: 10px;
    margin-top: 2px;
}

/* ── Street label ─────────────────────────────────────────────────────── */
.street-label {
    position: absolute;
    bottom: 14%; left: 50%;
    transform: translateX(-50%);
    color: rgba(255,255,255,0.45);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
}

/* ── Community cards ──────────────────────────────────────────────────── */
.community-cards {
    position: absolute;
    top: 44%; left: 50%;
    transform: translate(-50%, -50%);
    display: flex;
    gap: 6px;
}

/* ── Playing cards ────────────────────────────────────────────────────── */
.card {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    width: 50px; height: 70px;
    background: linear-gradient(to bottom, #fff, #f4f4f4);
    border: 1.5px solid #bbb;
    border-radius: 6px;
    font-weight: 700;
    box-shadow: 1px 2px 6px rgba(0,0,0,0.30);
}
.card .rank { font-size: 19px; line-height: 1; }
.card .suit { font-size: 15px; line-height: 1; margin-top: 1px; }

.card-sm {
    width: 38px; height: 52px;
}
.card-sm .rank { font-size: 14px; }
.card-sm .suit { font-size: 11px; }

.card-back {
    background: repeating-linear-gradient(
        135deg,
        #1a237e, #1a237e 4px,
        #283593 4px, #283593 8px
    );
    border: 2px solid #3949ab;
}

/* ── Seat containers ──────────────────────────────────────────────────── */
.seat {
    position: absolute;
    transform: translate(-50%, -50%);
    text-align: center;
    z-index: 10;
}

/* ── Player box ───────────────────────────────────────────────────────── */
.player-box {
    background: rgba(18, 18, 35, 0.92);
    border: 2px solid #555;
    border-radius: 10px;
    padding: 6px 12px 5px;
    min-width: 115px;
    color: #eee;
    position: relative;
    transition: border-color 0.15s, box-shadow 0.15s;
}
.player-box.hero {
    border-color: #FFD700;
    box-shadow: 0 0 10px rgba(255,215,0,0.35);
}
.player-box.active {
    border-color: #00ff88;
    box-shadow: 0 0 14px rgba(0,255,136,0.5);
}
.player-box.hero.active {
    border-color: #00ff88;
    box-shadow: 0 0 14px rgba(0,255,136,0.5), 0 0 6px rgba(255,215,0,0.25);
}
.player-box.folded {
    opacity: 0.45;
}

.p-pos {
    position: absolute;
    top: -9px; right: 6px;
    font-size: 9px;
    color: #FFD700;
    font-weight: 700;
    background: rgba(0,0,0,0.7);
    padding: 1px 5px;
    border-radius: 4px;
    letter-spacing: 0.5px;
}
.p-name {
    font-size: 12px;
    font-weight: 700;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 110px;
}
.p-stack {
    font-size: 11px;
    color: #b0b0b0;
    margin-top: 1px;
}
.p-action {
    font-size: 11px;
    color: #4fc3f7;
    margin-top: 2px;
    font-weight: 600;
}
.p-won {
    font-size: 12px;
    color: #2ecc71;
    font-weight: 700;
    margin-top: 2px;
}

/* ── Hole cards row ───────────────────────────────────────────────────── */
.hole-cards {
    display: flex;
    gap: 3px;
    justify-content: center;
    margin-top: 4px;
}

/* ── Dealer button ────────────────────────────────────────────────────── */
.dealer-btn {
    position: absolute;
    top: -8px; left: -8px;
    width: 22px; height: 22px;
    background: #FFD700;
    color: #000;
    font-weight: 800;
    font-size: 11px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 1px 1px 4px rgba(0,0,0,0.5);
    z-index: 20;
}

/* ── Bet chips ────────────────────────────────────────────────────────── */
.bet-chip {
    position: absolute;
    transform: translate(-50%, -50%);
    background: rgba(0,0,0,0.65);
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 10px;
    border: 1px solid rgba(255,255,255,0.25);
    white-space: nowrap;
    z-index: 5;
}
"""

_HTML_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head>
<body>
<div class="poker-container">
    <div class="table-felt">
        <div class="pot-area">
            <div class="pot-display">Pot: ${pot}</div>
            {stp_note}
        </div>
        <div class="community-cards">{community}</div>
        <div class="street-label">{street}</div>
    </div>
    {players}
    {bets}
</div>
</body></html>"""


# ═════════════════════════════════════════════════════════════════════════════
# Action log renderer
# ═════════════════════════════════════════════════════════════════════════════

def render_action_log(engine: HandReplayEngine, current_index: int) -> None:
    """Display action log with current action highlighted."""
    hand = engine.hand_data
    lines: list[str] = []

    # Blinds header
    for p in hand.players:
        if p.position == "SB":
            lines.append(f'<div class="log-info">{_esc(p.name)} posts SB ${_fmt_f(float(hand.small_blind))}</div>')
        if p.position == "BB":
            lines.append(f'<div class="log-info">{_esc(p.name)} posts BB ${_fmt_f(float(hand.big_blind))}</div>')

    # STP is a synthetic state at index 1; highlight it when on that step
    has_stp = float(hand.stp_amount) > 0
    if has_stp:
        stp_current = (current_index == 1)
        stp_cls = "log-current" if stp_current else "log-info"
        stp_prefix = "▶ " if stp_current else ""
        lines.append(f'<div class="{stp_cls}">{stp_prefix}Splash the Pot: ${_fmt_f(float(hand.stp_amount))} added to pot</div>')

    # Actions start at state index 2 when STP is present, else 1
    action_offset = 2 if has_stp else 1

    cur_street: str | None = None
    for i, action in enumerate(hand.actions):
        # Street header
        if action.street != cur_street:
            cur_street = action.street
            bc = STREET_BOARD_COUNT.get(cur_street, 0)
            board_str = " ".join(hand.board_cards[:bc])
            if board_str:
                lines.append(f'<div class="log-street">── {cur_street} [{board_str}] ──</div>')
            else:
                lines.append(f'<div class="log-street">── {cur_street} ──</div>')

        # Action line
        ai_tag = " (all-in)" if action.is_all_in else ""
        at = action.action_type
        if at == "fold":
            text = f"{_esc(action.player_name)} folds"
        elif at == "check":
            text = f"{_esc(action.player_name)} checks"
        elif at == "call":
            text = f"{_esc(action.player_name)} calls ${_fmt_f(float(action.amount))}{ai_tag}"
        elif at == "bet":
            text = f"{_esc(action.player_name)} bets ${_fmt_f(float(action.amount))}{ai_tag}"
        elif at == "raise":
            if action.raise_to is not None:
                text = f"{_esc(action.player_name)} raises to ${_fmt_f(float(action.raise_to))}{ai_tag}"
            else:
                text = f"{_esc(action.player_name)} raises ${_fmt_f(float(action.amount))}{ai_tag}"
        else:
            text = f"{_esc(action.player_name)}: {at}"

        is_current = (i + action_offset) == current_index
        cls = "log-current" if is_current else "log-action"
        prefix = "▶ " if is_current else "  "
        lines.append(f'<div class="{cls}">{prefix}{text}</div>')

    # Epilogue events: showdown reveals then pot award
    for event in engine.epilogue_events:
        ev_idx = event["index"]
        if ev_idx > current_index:
            break
        is_ev_current = ev_idx == current_index
        cls = "log-current" if is_ev_current else "log-action"
        prefix = "▶ " if is_ev_current else "  "
        if event["type"] == "runout":
            street = event.get("street", "")
            cards = event.get("cards", "")
            lines.append(f'<div class="log-street">── {_esc(street)} [{_esc(cards)}] ──</div>')
        elif event["type"] == "reveal":
            cards = event.get("cards", "?")
            text = f"{_esc(event['name'])} shows {_esc(cards)}"
            lines.append(f'<div class="{cls}">{prefix}{text}</div>')
        elif event["type"] == "award":
            lines.append('<div class="log-street">── RESULTS ──</div>')
            for p in hand.players:
                if float(p.won_amount) > 0:
                    won_cls = "log-current" if is_ev_current else "log-won"
                    lines.append(
                        f'<div class="{won_cls}">{prefix}{_esc(p.name)} wins '
                        f'${_fmt_f(float(p.won_amount))}</div>'
                    )

    log_html = "\n".join(lines)

    st.markdown(f"""
    <div style="
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: 13px;
        line-height: 1.65;
        max-height: 440px;
        overflow-y: auto;
        padding: 12px 14px;
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
    ">
    <style>
        .log-info {{ color: #8b949e; }}
        .log-street {{ color: #FFD700; font-weight: 700; margin-top: 8px; margin-bottom: 2px; }}
        .log-action {{ color: #c9d1d9; }}
        .log-current {{
            color: #58d68d;
            font-weight: 700;
            background: rgba(88,214,141,0.08);
            padding: 1px 4px;
            border-radius: 3px;
            border-left: 3px solid #58d68d;
        }}
        .log-won {{ color: #2ecc71; font-weight: 700; }}
    </style>
    {log_html}
    </div>
    """, unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
# Main UI — Two-column layout: Table (left) | Action Log (right)
# ═════════════════════════════════════════════════════════════════════════════

col_table, col_log = st.columns([2.5, 1])

with col_table:
    # ── Render the poker table ────────────────────────────────────────
    table_html = render_table_html(state, show_hero, show_results)
    components.html(table_html, height=525, scrolling=False)

    # ── Action description ────────────────────────────────────────────
    if state.action_text:
        st.markdown(
            f'<div style="text-align:center; color:#8b949e; font-size:14px; '
            f'margin: -8px 0 8px;">'
            f'{_esc(state.action_text)}</div>',
            unsafe_allow_html=True,
        )

    # ── Replay controls ───────────────────────────────────────────────
    ctrl_cols = st.columns(5, gap="small")

    with ctrl_cols[0]:
        if st.button("⏮ Start", use_container_width=True,
                      help="Go to beginning (deal)"):
            st.session_state.action_index = 0
            st.session_state.is_playing = False
            st.rerun()
    with ctrl_cols[1]:
        if st.button("◀ Back", use_container_width=True,
                      disabled=(idx <= 0),
                      help="Step backward one action"):
            st.session_state.action_index = max(0, idx - 1)
            st.session_state.is_playing = False
            st.rerun()
    with ctrl_cols[2]:
        _play_label = "⏸ Pause" if st.session_state.is_playing else "▶ Play"
        if st.button(_play_label, use_container_width=True,
                      help="Auto-play actions at the selected speed; pause to stop"):
            st.session_state.is_playing = not st.session_state.is_playing
            st.rerun()
    with ctrl_cols[3]:
        if st.button("Fwd ▶", use_container_width=True,
                      disabled=(idx >= engine.max_index),
                      help="Step forward one action"):
            st.session_state.action_index = min(engine.max_index, idx + 1)
            st.session_state.is_playing = False
            st.rerun()
    with ctrl_cols[4]:
        if st.button("End ⏭", use_container_width=True,
                      help="Skip to end"):
            st.session_state.action_index = engine.max_index
            st.session_state.is_playing = False
            st.rerun()

    # Speed slider — own row for space
    st.session_state.play_speed = st.select_slider(
        "Speed",
        options=[0.5, 1.0, 1.5, 2.0, 3.0],
        value=st.session_state.play_speed,
        format_func=lambda x: f"{x}s/action",
        help="Seconds between each action during auto-play",
    )

    # ── Slider for quick scrubbing ────────────────────────────────────
    if engine.max_index > 0:
        new_idx = st.slider(
            "Action",
            min_value=0,
            max_value=engine.max_index,
            value=idx,
            format=f"Step %d / {engine.max_index}",
            label_visibility="collapsed",
        )
        if new_idx != idx:
            st.session_state.action_index = new_idx
            st.rerun()

    # ── Jump-to-street buttons ────────────────────────────────────────
    street_indices = engine.get_street_indices()
    if len(street_indices) > 1:
        scols = st.columns(len(street_indices), gap="small")
        for i, (street_name, s_idx) in enumerate(street_indices.items()):
            with scols[i]:
                bc = STREET_BOARD_COUNT.get(street_name, 0)
                board_preview = " ".join(hd.board_cards[:bc]) if bc > 0 else ""
                label = f"{street_name}"
                if board_preview:
                    label += f" [{board_preview}]"
                if st.button(
                    label, key=f"jmp_{street_name}",
                    use_container_width=True,
                ):
                    st.session_state.action_index = s_idx
                    st.rerun()

with col_log:
    # ── Action log ────────────────────────────────────────────────────
    st.markdown(
        f"**Action Log** · Step {idx}/{engine.max_index} · {state.street}"
    )
    render_action_log(engine, idx)


# ═════════════════════════════════════════════════════════════════════════════
# Auto-play loop
# ═════════════════════════════════════════════════════════════════════════════

if st.session_state.is_playing:
    time.sleep(st.session_state.play_speed)
    if idx < engine.max_index:
        # Advance one action within the current hand
        st.session_state.action_index = idx + 1
        st.rerun()
    else:
        # Finished this hand — advance to the next hand in the queue
        next_list_idx = cur_list_idx + 1
        if next_list_idx < len(hand_ids):
            st.session_state.hand_list_idx = next_list_idx
            st.session_state.action_index = 0
            st.session_state._prev_hand_id = hand_ids[next_list_idx]
            st.rerun()
        else:
            # No more hands left — stop playing
            st.session_state.is_playing = False
            st.rerun()
