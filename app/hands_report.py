"""
BetRivers Poker Tracker — Hands in Report (Streamlit page).

Entry-point page that wires the three layers together:
    Data Access  →  View Model  →  UI View

Launch standalone::

    streamlit run app/hands_report.py

Or use as part of the multi-page app via ``main.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path so `app.*` imports work
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.ui.views.hands_report_view import render_hands_report

render_hands_report()
