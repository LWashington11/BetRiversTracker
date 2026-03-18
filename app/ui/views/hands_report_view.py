"""
Hands-in-Report — View Layer.

Renders the filter bar (top of main content), data grid with row
selection, and selection controls (Open in Replayer).

Filters are displayed as a compact horizontal bar at the top of the
page, not in the sidebar. This keeps filters visible without scrolling.

Supports navigation from the Dashboard's Session Report:
    session_state["hr_prefill_session_date"]  →  auto-selects that session.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Any

import pandas as pd
import streamlit as st

from app.prefs import get_col_setting, save_col_setting
from app.data_access.hands_repository import (
    HandFilter,
    fetch_hands_for_report,
    fetch_filter_options,
)
from app.viewmodels.hands_grid_vm import transform_hands, to_dataframe
from app.constants import HANDS_REPORT_PAGE_SIZE, CACHE_TTL


# ═════════════════════════════════════════════════════════════════════════════
# Constants
# ═════════════════════════════════════════════════════════════════════════════

_PAGE_SIZE = HANDS_REPORT_PAGE_SIZE


# ═════════════════════════════════════════════════════════════════════════════
# Cached data loaders
# ═════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=CACHE_TTL, show_spinner="Querying hands…")
def _cached_fetch_hands(hero_name, session_date, session_dates_tuple,
                        positions_tuple, stakes_tuple, date_from, date_to,
                        game_type, min_bb, max_bb, offset, limit):
    """Cacheable wrapper around fetch_hands_for_report."""
    filters = HandFilter(
        session_date=session_date,
        session_dates=list(session_dates_tuple) if session_dates_tuple else None,
        positions=list(positions_tuple) if positions_tuple else None,
        stakes=list(stakes_tuple) if stakes_tuple else None,
        date_from=date_from,
        date_to=date_to,
        game_type=game_type,
        min_net_won_bb=min_bb,
        max_net_won_bb=max_bb,
    )
    rows, total = fetch_hands_for_report(
        hero_name, filters, limit=limit, offset=offset,
    )
    grid_rows = transform_hands(rows, filters)
    df = to_dataframe(grid_rows)
    return df, total

@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading filter options…")
def _cached_filter_options(hero_name):
    """Fetch session dates, stakes, and positions in a single DB session."""
    return fetch_filter_options(hero_name)


# ═════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═════════════════════════════════════════════════════════════════════════════

def render_hands_report() -> None:
    """Top-level function — called from the Streamlit page file."""

    _hero = st.session_state.get("hero_name", "")

    st.markdown("### 📋 Hands Report")

    # ── Top filter bar ───────────────────────────────────────────────
    filters = _render_filters(_hero)

    # If user cleared all sessions, show no hands
    chosen_sessions = st.session_state.get("hr_sessions", [])
    if chosen_sessions is not None and isinstance(chosen_sessions, list) and len(chosen_sessions) == 0:
        st.info("No sessions selected. Please select one or more sessions to view hands.")
        return

    # ── Pagination state ──────────────────────────────────────────
    # Reset page to 0 when filters change.
    fp = _filter_fingerprint(filters)
    if fp != st.session_state.get("_hr_last_fp"):
        st.session_state["_hr_last_fp"] = fp
        st.session_state["hr_page"] = 0
    if "hr_page" not in st.session_state:
        st.session_state["hr_page"] = 0
    page = st.session_state["hr_page"]
    offset = page * _PAGE_SIZE

    # ── Fetch & transform (cached) ──────────────────────────────────
    df, total = _cached_fetch_hands(
        _hero,
        filters.session_date,
        tuple(filters.session_dates) if filters.session_dates else None,
        tuple(filters.positions) if filters.positions else None,
        tuple(filters.stakes) if filters.stakes else None,
        filters.date_from,
        filters.date_to,
        filters.game_type if hasattr(filters, "game_type") else None,
        filters.min_net_won_bb,
        filters.max_net_won_bb,
        offset,
        _PAGE_SIZE,
    )

    if total == 0:
        st.info("No hands match the current filters.")
        return

    displayed = len(df)

    # Active filter summary
    _show_active_filters(filters, total, displayed, page, _PAGE_SIZE)

    # ── Pagination controls (top) ──────────────────────────────
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    if total_pages > 1:
        _render_pagination(page, total_pages, key_suffix="top")

    # ── Column selector ────────────────────────────────────────
    if "vis_cols_hands" not in st.session_state:
        st.session_state["vis_cols_hands"] = get_col_setting(
            "cols_hands", _DEFAULT_HANDS_COLS
        )
    _saved_hands = get_col_setting("cols_hands", _DEFAULT_HANDS_COLS)
    with st.popover("⚙ Columns"):
        if st.button("↩ Reset", key="reset_cols_hands"):
            st.session_state["vis_cols_hands"] = list(_DEFAULT_HANDS_COLS)
            save_col_setting("cols_hands", list(_DEFAULT_HANDS_COLS))
        st.multiselect(
            "Visible columns",
            options=_ALL_HANDS_COLS,
            key="vis_cols_hands",
            label_visibility="collapsed",
            on_change=lambda: save_col_setting(
                "cols_hands",
                st.session_state.get("vis_cols_hands", []),
            ),
        )
    _vis_hands = st.session_state.get("vis_cols_hands", _DEFAULT_HANDS_COLS)

    # ── Open in Replayer (above grid) ────────────────────────────────
    # Read the previous grid selection from session state so the button
    # is visible before the user has to scroll past the table.
    _above_ids = _get_grid_selection_from_state(df)
    _render_selection_controls(_above_ids, df, location="above")

    # ── Render grid ─────────────────────────────────────────
    selected_hand_ids = _render_grid(df, visible_cols=_vis_hands)

    # ── Selection controls (below grid) ───────────────────────────────────
    _render_selection_controls(selected_hand_ids, df, location="below")

    # ── Pagination controls (bottom) ────────────────────────────────
    if total_pages > 1:
        _render_pagination(page, total_pages, key_suffix="bottom")


# ═════════════════════════════════════════════════════════════════════════════
# Inline filter bar (main content area)
# ═════════════════════════════════════════════════════════════════════════════

def _render_filters(hero_name: str) -> HandFilter:
    """Draw compact filter bar at the top of the page and return a HandFilter."""

    session_dates, stakes_opts, pos_opts = _cached_filter_options(hero_name)

    # Check for prefill from Dashboard session drill-down
    prefill_session_date = st.session_state.pop("hr_prefill_session_date", None)
    prefill_session_dates = st.session_state.pop("hr_prefill_session_dates", None)

    # ── Pre-seed session selection in state ──────────────────────────
    session_labels = [s["label"] for s in session_dates]
    _label_to_date = {s["label"]: s["date"] for s in session_dates}

    if prefill_session_dates:
        # Navigation from Dashboard with multiple sessions
        st.session_state["hr_sessions"] = [
            s["label"] for s in session_dates
            if s["date"] in prefill_session_dates
        ]
    elif prefill_session_date:
        # Navigation from Dashboard with a single session
        st.session_state["hr_sessions"] = [
            s["label"] for s in session_dates
            if (
                s["date"] == prefill_session_date
                if isinstance(s["date"], date_type)
                else str(s["date"]) == str(prefill_session_date)
            )
        ]
    elif "hr_sessions" not in st.session_state:
        # First load: default to the most recent session
        st.session_state["hr_sessions"] = session_labels[:1]

    # Row 1: Session(s) — full width (labels are long)
    st.multiselect(
        "Session(s)",
        options=session_labels,
        placeholder="All sessions",
        key="hr_sessions",
    )
    chosen_sessions: list[str] = st.session_state.get("hr_sessions", [])
    selected_dates = [_label_to_date[lbl] for lbl in chosen_sessions if lbl in _label_to_date]

    # Row 2: Stakes + Position + Date range
    fc2, fc3, fc4, fc5 = st.columns([2, 2, 1, 1], gap="small")

    with fc2:
        stakes = st.multiselect(
            "Stakes", options=stakes_opts, default=[],
            placeholder="All stakes", key="hr_stakes",
        )
    with fc3:
        positions = st.multiselect(
            "Position", options=pos_opts, default=[],
            placeholder="All positions", key="hr_positions",
        )
    with fc4:
        date_from = st.date_input(
            "From", value=None, key="hr_date_from", format="MM/DD/YYYY",
        )
    with fc5:
        date_to = st.date_input(
            "To", value=None, key="hr_date_to", format="MM/DD/YYYY",
        )

    # Prefill support from dashboard links
    if "hr_prefill_stakes" in st.session_state and not stakes:
        prefill = st.session_state.pop("hr_prefill_stakes")
        if isinstance(prefill, list):
            stakes = prefill

    if "hr_prefill_positions" in st.session_state and not positions:
        prefill = st.session_state.pop("hr_prefill_positions")
        if isinstance(prefill, list):
            positions = prefill

    # Additional numeric filters in a popover
    with st.popover("🔍 More Filters"):
        min_bb = st.number_input(
            "Min Net Won (bb)", value=None, step=1.0, key="hr_min_bb",
            help="Only show hands where hero won at least this many bb",
        )
        max_bb = st.number_input(
            "Max Net Won (bb)", value=None, step=1.0, key="hr_max_bb",
            help="Only show hands where hero won at most this many bb",
        )

    return HandFilter(
        session_date=None,
        session_dates=selected_dates or None,
        positions=positions or None,
        stakes=stakes or None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
        min_net_won_bb=min_bb,
        max_net_won_bb=max_bb,
    )


def _filter_fingerprint(f: HandFilter) -> tuple:
    """Return a hashable tuple for detecting filter changes."""
    return (
        f.session_date,
        tuple(f.session_dates) if f.session_dates else None,
        tuple(f.positions) if f.positions else None,
        tuple(f.stakes) if f.stakes else None,
        f.date_from, f.date_to,
        f.min_net_won_bb, f.max_net_won_bb,
    )


def _show_active_filters(
    filters: HandFilter, total_db: int, displayed: int,
    page: int = 0, page_size: int = 200,
) -> None:
    """Show a summary bar of active filters."""
    chips: list[str] = []
    if filters.session_dates:
        chips.append(
            f"Session(s): {', '.join(str(d) for d in filters.session_dates)}"
        )
    elif filters.session_date:
        chips.append(f"Session: {filters.session_date}")
    if filters.stakes:
        chips.append(f"Stakes: {', '.join(filters.stakes)}")
    if filters.positions:
        chips.append(f"Position: {', '.join(filters.positions)}")
    if filters.date_from or filters.date_to:
        fr = str(filters.date_from) if filters.date_from else "…"
        to = str(filters.date_to) if filters.date_to else "…"
        chips.append(f"Date: {fr} → {to}")
    if filters.min_net_won_bb is not None:
        chips.append(f"Min bb: {filters.min_net_won_bb}")
    if filters.max_net_won_bb is not None:
        chips.append(f"Max bb: {filters.max_net_won_bb}")

    filter_text = "  ·  ".join(chips) if chips else "None"
    start = page * page_size + 1 if total_db > 0 else 0
    end = min(start + displayed - 1, total_db)
    page_info = f"**{start:,}–{end:,}** of {total_db:,}" if total_db else "0"
    st.caption(
        f"Showing {page_info} matching hands  |  Filters: {filter_text}"
    )


def _render_pagination(page: int, total_pages: int, key_suffix: str = "") -> None:
    """Render Previous / Page X of Y / Next controls."""
    c1, c2, c3 = st.columns([1, 2, 1], gap="small")
    with c1:
        if st.button("← Previous", disabled=(page <= 0),
                      key=f"pg_prev_{key_suffix}",
                      use_container_width=True):
            st.session_state["hr_page"] = page - 1
            st.rerun()
    with c2:
        st.markdown(
            f"<div style='text-align:center;padding:0.35em 0'>"
            f"Page {page + 1} of {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with c3:
        if st.button("Next →", disabled=(page >= total_pages - 1),
                      key=f"pg_next_{key_suffix}",
                      use_container_width=True):
            st.session_state["hr_page"] = page + 1
            st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# Grid rendering — native st.dataframe with row selection
# ═════════════════════════════════════════════════════════════════════════════

# All hand columns available for display (excludes internal _hand_id, _db_id)
_ALL_HANDS_COLS = [
    "Time", "Stakes", "Stack (bb)", "Cards", "Position", "Line",
    "Board", "Net Won ($)", "Net Won (bb)",
    "STP ($)",
    "PF Line",
]
# By default show every column (user may hide / reorder via the column selector)
_DEFAULT_HANDS_COLS = list(_ALL_HANDS_COLS)


def _render_grid(df: pd.DataFrame, visible_cols: list[str] | None = None) -> list[int]:
    """
    Render the data grid with native st.dataframe row selection.

    Returns a list of selected hand_id values.
    """
    if df.empty:
        return []

    if visible_cols is None:
        visible_cols = _ALL_HANDS_COLS
    display_df = df[[c for c in visible_cols if c in df.columns]].copy()

    col_config = {
        "Time": st.column_config.TextColumn("Time", width="small"),
        "Stakes": st.column_config.TextColumn("Stakes", width="small"),
        "Stack (bb)": st.column_config.NumberColumn(
            "Stack (bb)", width="small",
        ),
        "Cards": st.column_config.TextColumn("Cards", width="small"),
        "Position": st.column_config.TextColumn("Pos", width="small"),
        "Line": st.column_config.TextColumn("Line", width="small"),
        "Board": st.column_config.TextColumn("Board", width="medium"),
        "Net Won ($)": st.column_config.NumberColumn(
            "Net Won ($)", width="small",
        ),
        "Net Won (bb)": st.column_config.NumberColumn(
            "Net Won (bb)", width="small",
        ),
        "STP ($)": st.column_config.NumberColumn(
            "STP ($)", width="small",
        ),
        "PF Line": st.column_config.TextColumn("PF Line", width="small"),
    }

    # Apply style formatting for commas in money/number columns
    styled_df = display_df.style.format({
        "Stack (bb)": "{:,.1f}",
        "Net Won ($)": "${:,.2f}",
        "Net Won (bb)": "{:,.2f}",
        "STP ($)": lambda v: f"${v:,.2f}" if v else "",
    })

    selection = st.dataframe(
        styled_df,
        column_config=col_config,
        width='stretch',
        height=560,
        hide_index=True,
        on_select="rerun",
        selection_mode="multi-row",
        key="hands_report_grid",
    )

    selected_indices = (
        selection.selection.rows if selection and selection.selection else []
    )
    if selected_indices and "_hand_id" in df.columns:
        return [
            int(df.iloc[i]["_hand_id"])
            for i in selected_indices
            if i < len(df)
        ]
    return []


def _get_grid_selection_from_state(df: pd.DataFrame) -> list[int]:
    """Return selected hand IDs from the last known grid state (session state)."""
    raw = st.session_state.get("hands_report_grid")
    if raw is None or not hasattr(raw, "selection"):
        return []
    rows = raw.selection.rows if raw.selection else []
    if rows and "_hand_id" in df.columns:
        return [int(df.iloc[i]["_hand_id"]) for i in rows if i < len(df)]
    return []


# ═════════════════════════════════════════════════════════════════════════════
# Selection controls
# ═════════════════════════════════════════════════════════════════════════════

def _render_selection_controls(
    selected_ids: list[int],
    df: pd.DataFrame,
    location: str = "above",
) -> None:
    """Render the Open in Replayer button + summary of selected hands.
    
    Args:
        selected_ids: List of selected hand IDs.
        df: Full hands dataframe.
        location: Either 'above' or 'below' the grid (for unique button key).
    """

    n = len(selected_ids) if selected_ids else 0

    col1, col2, col3 = st.columns([3, 3, 4], gap="small")

    with col1:
        st.markdown(f"**{n}** hand{'s' if n != 1 else ''} selected")

    with col2:
        open_clicked = st.button(
            "🃏 Open in Replayer",
            disabled=(n == 0),
            type="primary",
            use_container_width=True,
            key=f"open_replayer_btn_{location}",
        )

    if open_clicked and selected_ids:
        st.session_state["replayer_hand_ids"] = selected_ids
        st.session_state["hand_list_idx"] = 0
        st.session_state["_prev_hand_id"] = None
        st.session_state["action_index"] = 0
        st.session_state["_from_hands_report"] = True
        try:
            st.switch_page("replayer.py")
        except Exception:
            st.success(
                f"Selected {len(selected_ids)} hand(s) for replay. "
                "Navigate to the **Hand Replayer** page."
            )

    # Summary of selected hands
    if selected_ids and not df.empty:
        sel_df = df[df["_hand_id"].isin(selected_ids)]
        if not sel_df.empty:
            total_net = sel_df["Net Won ($)"].sum()
            total_bb = sel_df["Net Won (bb)"].sum()
            color = "#2ecc71" if total_net >= 0 else "#e74c3c"
            sign = "+" if total_net >= 0 else ""
            with col3:
                st.markdown(
                    f'Selected total: '
                    f'<span style="color:{color};font-weight:bold;">'
                    f'{sign}${total_net:,.2f}</span>'
                    f' ({sign}{total_bb:,.1f} bb)',
                    unsafe_allow_html=True,
                )
