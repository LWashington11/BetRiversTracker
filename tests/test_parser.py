"""
Tests for the BetRivers parser — verifies the sample hand is correctly parsed.
Run with:  pytest tests/ -v
"""

import sys
from pathlib import Path
from decimal import Decimal

# Ensure project root is on path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.parser import parse_file, parse_hand


SAMPLE_FILE = Path(__file__).resolve().parent.parent / "hand_histories" / "sample.txt"


def test_parse_sample_file():
    hands = parse_file(SAMPLE_FILE)
    assert len(hands) == 1, f"Expected 1 hand, got {len(hands)}"


def test_hand_header():
    hands = parse_file(SAMPLE_FILE)
    h = hands[0]
    assert h["hand_id"] == 42133993
    assert h["small_blind"] == Decimal("0.50")
    assert h["big_blind"] == Decimal("1.00")
    assert h["played_date"].isoformat() == "2026-02-06"


def test_table_info():
    h = parse_file(SAMPLE_FILE)[0]
    assert h["table_id"] == "682729"
    assert h["max_seats"] == 6
    assert h["button_seat"] == 6


def test_stp():
    h = parse_file(SAMPLE_FILE)[0]
    assert h["stp_amount"] == Decimal("1.00")


def test_seats():
    h = parse_file(SAMPLE_FILE)[0]
    seats = h["seats"]
    # 6 seats listed
    assert len(seats) == 6

    # testPlayer at seat 1
    assert seats[1]["name"] == "testPlayer"
    assert seats[1]["stack"] == Decimal("104.08")
    assert seats[1]["hole_cards"] == "8d Ks"

    # Dario T sitting out
    assert seats[2]["is_sitting_out"] is True


def test_hero_net_won():
    h = parse_file(SAMPLE_FILE)[0]
    hero = h["seats"][1]
    # Hero posted SB $0.50 then folded, so net = 0 - 0.50 = -0.50
    assert hero["net_won"] == Decimal("-0.50")


def test_winner():
    h = parse_file(SAMPLE_FILE)[0]
    wade = h["seats"][5]
    assert wade["won_amount"] == Decimal("31.79")


def test_investment_tracking():
    """Verify total_invested is correct for all active players."""
    h = parse_file(SAMPLE_FILE)[0]

    # testPlayer: posted SB $0.50, folded → total_invested = $0.50
    hero = h["seats"][1]
    assert hero["total_invested"] == Decimal("0.50")

    # Collins V: posted BB $1.00, called $1.50, bet $4.14, bet $9.36
    # total = 1.00 + 1.50 + 4.14 + 9.36 = 16.00
    collins = h["seats"][3]
    assert collins["total_invested"] == Decimal("16.00")

    # Wade S: raised to $2.50, called $4.14, called $9.36
    # total = 2.50 + 4.14 + 9.36 = 16.00
    wade = h["seats"][5]
    assert wade["total_invested"] == Decimal("16.00")

    # Eliza S: folded preflop, no blind → total_invested = 0
    eliza = h["seats"][4]
    assert eliza["total_invested"] == Decimal("0")


def test_net_won_winner():
    """Winner's net_won should be positive."""
    h = parse_file(SAMPLE_FILE)[0]
    wade = h["seats"][5]
    # Wade won $31.79, invested $16.00 → net = $15.79
    assert wade["net_won"] == Decimal("15.79")


# ── Tests using an inline hand with uncalled bet ────────────────────────────

UNCALLED_BET_HAND = """\
BetRivers Poker Hand #99999999: Hold'em No Limit ($0.50/$1.00) - 2026/02/10 15:30 EST
Table ID '123456' 6-Max Seat #3 is the button
Seat 1: Player A ($100.00 in chips)
Seat 2: Player B ($100.00 in chips)
Seat 3: testPlayer ($100.00 in chips)
Player A: posts small blind $0.50
Player B: posts big blind $1.00
*** HOLE CARDS ***
Dealt to testPlayer [As Kd]
testPlayer: raises $1.50 to $2.50
Player A: folds
Player B: folds
Uncalled bet ($1.50) returned to testPlayer
testPlayer collected $2.50 from pot
*** SUMMARY ***
Total pot $2.50 | Rake $0.00
Board []
Seat 1: Player A (small blind) folded before Flop
Seat 2: Player B (big blind) folded before Flop
Seat 3: testPlayer (button) collected ($2.50)
"""


def test_uncalled_bet_returned():
    """When hero raises and all fold, uncalled portion is returned."""
    h = parse_hand(UNCALLED_BET_HAND)
    assert h is not None
    hero = h["seats"][3]

    # Hero raised to $2.50, $1.50 returned → effective investment = $1.00
    assert hero["total_invested"] == Decimal("1.00")
    assert hero["won_amount"] == Decimal("2.50")
    # Net = 2.50 won - 1.00 invested = +1.50
    assert hero["net_won"] == Decimal("1.50")


def test_actions():
    h = parse_file(SAMPLE_FILE)[0]
    actions = h["actions"]
    assert len(actions) > 0
    # First non-blind action should be Eliza folds
    action_types = [(a["player_name"], a["action_type"], a["street"]) for a in actions]
    assert ("Eliza S", "fold", "PREFLOP") in action_types
    assert ("testPlayer", "fold", "PREFLOP") in action_types


def test_pot_summary():
    h = parse_file(SAMPLE_FILE)[0]
    assert h["total_pot"] == Decimal("33.50")
    assert h["main_pot"] == Decimal("32.50")
    assert h["rake"] == Decimal("1.71")


def test_board():
    h = parse_file(SAMPLE_FILE)[0]
    assert h["board"] == "Kh Ac 4s 2h As"


def test_showdown_flags():
    h = parse_file(SAMPLE_FILE)[0]
    collins = h["seats"][3]
    wade = h["seats"][5]
    assert collins["went_to_showdown"] is True
    assert wade["went_to_showdown"] is True
    # Hero did NOT go to showdown
    hero = h["seats"][1]
    assert hero["went_to_showdown"] is False
