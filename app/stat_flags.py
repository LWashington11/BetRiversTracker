"""
Per-hand stat flag computation.

Computes boolean/numeric flags for a single hand_player participation.
These flags are stored on the hand_players row at import time and used
to build aggregation tables without ever scanning the actions table.

This module works with plain dicts/lists — no ORM dependency — so it can
be called from both the importer (parsed data) and the backfill command
(DB data converted to dicts).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

_log = logging.getLogger(__name__)


def compute_hand_flags(
    hp_id: int,
    position: str,
    net_won: Decimal,
    won_amount: Decimal,
    total_invested: Decimal,
    went_to_showdown: bool,
    big_blind: Decimal,
    total_pot: Decimal,
    rake: Decimal,
    stp_amount: Decimal,
    board: str | None,
    hero_actions: list[dict[str, Any]],
    all_preflop_actions: list[dict[str, Any]],
    sd_player_count: int,
    btn_hp_id: int | None,
) -> dict[str, Any]:
    """
    Compute all stat flags for one hand_player participation.

    Parameters
    ----------
    hp_id : int
        The hand_player.id for this player in this hand.
    position : str
        Player's position (SB, BB, UTG, MP, CO, BTN).
    net_won, won_amount, total_invested : Decimal
        Financial results for this hand.
    went_to_showdown : bool
        Whether this player went to showdown.
    big_blind : Decimal
        Big blind amount for this hand.
    total_pot, rake, stp_amount : Decimal
        Hand pot info for rake attribution.
    board : str | None
        Space-separated board cards (e.g. "Kh Ac 4s").
    hero_actions : list[dict]
        This player's actions: [{street, sequence, action_type, amount,
        raise_to, is_all_in}, ...].
    all_preflop_actions : list[dict]
        ALL players' preflop actions for this hand, sorted by sequence:
        [{hand_player_id, sequence, action_type, amount, is_all_in}, ...].
    sd_player_count : int
        Number of players who went to showdown in this hand.
    btn_hp_id : int | None
        The hand_player_id of the BTN player in this hand, or None.

    Returns
    -------
    dict with all flag fields matching HandPlayer columns.
    """
    bb = big_blind or Decimal("1")

    preflop_acts = [a for a in hero_actions if a["street"] == "PREFLOP"]
    postflop_acts = [a for a in hero_actions if a["street"] != "PREFLOP"]
    hero_pos = position or ""

    # ── Basic flags ──────────────────────────────────────────────────
    was_walk = (hero_pos == "BB" and not preflop_acts)
    was_vpip = any(
        a["action_type"] in ("call", "raise", "bet") for a in preflop_acts
    )
    was_pfr = any(a["action_type"] == "raise" for a in preflop_acts)

    # ── RFI / 3Bet / 4Bet / Fold to 3Bet ────────────────────────────
    raise_count = 0
    hero_action_num = 0
    hero_opened = False
    hero_3bet_this = False
    hero_had_3bet_opp = False
    hero_had_4bet_opp = False
    hero_4bet_this = False
    hero_faced_3bet = False
    hero_folded_to_3bet = False
    had_rfi_opp = False
    was_rfi = False

    for pa in all_preflop_actions:
        is_hero = pa["hand_player_id"] == hp_id

        if is_hero:
            hero_action_num += 1
            if hero_action_num == 1:
                if raise_count == 0:
                    had_rfi_opp = True
                    if pa["action_type"] == "raise":
                        was_rfi = True
                        hero_opened = True
                elif raise_count == 1:
                    hero_had_3bet_opp = True
                    if pa["action_type"] == "raise":
                        hero_3bet_this = True
                elif raise_count >= 2:
                    hero_had_4bet_opp = True
                    if pa["action_type"] == "raise":
                        hero_4bet_this = True
            else:
                if hero_opened and raise_count == 2:
                    hero_faced_3bet = True
                    hero_had_4bet_opp = True
                    if pa["action_type"] == "fold":
                        hero_folded_to_3bet = True
                    elif pa["action_type"] == "raise":
                        hero_4bet_this = True

        if pa["action_type"] == "raise":
            raise_count += 1

    # ── Fold to BTN steal ────────────────────────────────────────────
    faced_btn_steal = False
    folded_to_btn_steal = False
    if hero_pos in ("SB", "BB") and btn_hp_id is not None:
        btn_opened = False
        raises_before_btn = 0
        for pa in all_preflop_actions:
            if pa["hand_player_id"] == btn_hp_id:
                if raises_before_btn == 0 and pa["action_type"] == "raise":
                    btn_opened = True
                break
            if pa["action_type"] == "raise":
                raises_before_btn += 1

        if btn_opened:
            faced_btn_steal = True
            for pa in all_preflop_actions:
                if pa["hand_player_id"] == hp_id and pa["sequence"] > 0:
                    if pa["action_type"] == "fold":
                        folded_to_btn_steal = True
                    break

    # ── Saw flop ─────────────────────────────────────────────────────
    hero_folded_preflop = any(
        a["street"] == "PREFLOP" and a["action_type"] == "fold"
        for a in hero_actions
    )
    board_cards = len((board or "").split()) if board else 0
    saw_flop = (not hero_folded_preflop) and board_cards >= 3

    # ── Postflop aggression ──────────────────────────────────────────
    pf_bets_raises = 0
    pf_calls = 0
    pf_checks = 0
    for a in postflop_acts:
        if a["action_type"] in ("bet", "raise"):
            pf_bets_raises += 1
        elif a["action_type"] == "call":
            pf_calls += 1
        elif a["action_type"] == "check":
            pf_checks += 1

    # ── CBet ─────────────────────────────────────────────────────────
    last_raiser_hp_id = None
    for pa in all_preflop_actions:
        if pa["action_type"] == "raise":
            last_raiser_hp_id = pa["hand_player_id"]
    hero_was_pfa = last_raiser_hp_id == hp_id

    flop_acts_sorted = sorted(
        [a for a in hero_actions if a["street"] == "FLOP"],
        key=lambda a: a["sequence"],
    )
    hero_saw_flop_actions = len(flop_acts_sorted) > 0

    had_cbet_opp = False
    was_cbet = False
    if hero_was_pfa and hero_saw_flop_actions:
        first_flop = flop_acts_sorted[0]["action_type"]
        if first_flop in ("bet", "check"):
            had_cbet_opp = True
            if first_flop == "bet":
                was_cbet = True

    # ── All-in ───────────────────────────────────────────────────────
    hero_was_allin = any(a.get("is_all_in") for a in hero_actions)

    # ── Rake attribution ─────────────────────────────────────────────
    rake_from_won_val = Decimal("0")
    if won_amount and won_amount > 0:
        rake_h = rake or Decimal("0")
        total_distributed = (total_pot or Decimal("0")) - rake_h
        if total_distributed > 0:
            fraction = won_amount / total_distributed
            rake_from_won_val = (rake_h * fraction).quantize(Decimal("0.01"))
        else:
            rake_from_won_val = rake_h

    rake_attr_val = Decimal("0")
    if total_pot and total_pot > 0 and rake:
        stp = stp_amount or Decimal("0")
        player_pot = total_pot - stp
        if player_pot > 0:
            proportion = (total_invested or Decimal("0")) / player_pot
            rake_attr_val = (rake * proportion).quantize(Decimal("0.01"))

    # ── BB won ───────────────────────────────────────────────────────
    bb_won_val = (net_won or Decimal("0")) / bb

    return {
        "was_vpip": was_vpip,
        "was_pfr": was_pfr,
        "was_3bet": hero_3bet_this,
        "had_3bet_opp": hero_had_3bet_opp,
        "was_4bet": hero_4bet_this,
        "had_4bet_opp": hero_had_4bet_opp,
        "was_rfi": was_rfi,
        "had_rfi_opp": had_rfi_opp,
        "folded_to_3bet": hero_folded_to_3bet,
        "faced_3bet": hero_faced_3bet,
        "saw_flop": saw_flop,
        "was_cbet": was_cbet,
        "had_cbet_opp": had_cbet_opp,
        "folded_to_btn_steal": folded_to_btn_steal,
        "faced_btn_steal": faced_btn_steal,
        "was_walk": was_walk,
        "hero_was_allin": hero_was_allin,
        "allin_ev_diff": Decimal("0"),
        "postflop_bets_raises": pf_bets_raises,
        "postflop_calls": pf_calls,
        "postflop_checks": pf_checks,
        "rake_attributed": rake_attr_val,
        "rake_from_won": rake_from_won_val,
        "bb_won": bb_won_val,
        # Extra flags for aggregation (not stored on hand_players directly)
        "_went_to_showdown": went_to_showdown,
        "_net_won": net_won or Decimal("0"),
        "_won_at_sd": went_to_showdown and (net_won or 0) > 0,
        "_won_when_saw_flop": saw_flop and (net_won or 0) > 0,
        "_went_to_sd_from_flop": saw_flop and went_to_showdown,
        "_sd_won": (net_won or Decimal("0")) if went_to_showdown else Decimal("0"),
        "_nonsd_won": (net_won or Decimal("0")) if not went_to_showdown else Decimal("0"),
    }


# ── Aggregation delta: convert flags → increments for PlayerStatSummary ─────

def flags_to_summary_deltas(flags: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a single hand's flags dict into the incremental deltas
    to add to a PlayerStatSummary row.

    Returns a dict whose keys match PlayerStatSummary column names
    and values are the amounts to add (+1 or +amount).
    """
    return {
        "total_hands": 1,
        "walk_count": int(flags["was_walk"]),
        "vpip_count": int(flags["was_vpip"]),
        "pfr_count": int(flags["was_pfr"]),
        "rfi_count": int(flags["was_rfi"]),
        "rfi_opportunities": int(flags["had_rfi_opp"]),
        "three_bet_count": int(flags["was_3bet"]),
        "three_bet_opportunities": int(flags["had_3bet_opp"]),
        "four_bet_count": int(flags["was_4bet"]),
        "four_bet_opportunities": int(flags["had_4bet_opp"]),
        "fold_to_3bet_count": int(flags["folded_to_3bet"]),
        "fold_to_3bet_opportunities": int(bool(
            flags.get("faced_3bet") and flags.get("had_rfi_opp")
            and flags.get("was_rfi")
        )),
        "saw_flop_count": int(flags["saw_flop"]),
        "went_to_sd_count": int(flags["_went_to_showdown"]),
        "went_to_sd_from_flop_count": int(flags["_went_to_sd_from_flop"]),
        "won_at_sd_count": int(flags["_won_at_sd"]),
        "won_when_saw_flop_count": int(flags["_won_when_saw_flop"]),
        "cbet_count": int(flags["was_cbet"]),
        "cbet_opportunities": int(flags["had_cbet_opp"]),
        "fold_to_btn_steal_count": int(flags["folded_to_btn_steal"]),
        "fold_to_btn_steal_opportunities": int(flags["faced_btn_steal"]),
        "postflop_bets_raises": flags["postflop_bets_raises"],
        "postflop_calls": flags["postflop_calls"],
        "postflop_checks": flags["postflop_checks"],
        "net_won": flags["_net_won"],
        "sd_won": flags["_sd_won"],
        "nonsd_won": flags["_nonsd_won"],
        "bb_won_total": flags["bb_won"],
        "allin_ev_diff": flags["allin_ev_diff"],
        "rake_from_won": flags["rake_from_won"],
        "rake_attributed": flags["rake_attributed"],
    }
