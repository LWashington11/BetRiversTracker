"""
BetRivers Poker Tracker - Unofficial — Streamlit Dashboard

Designed with a compact layout:
- Compact filter bar at the top (visible immediately)
- Import as a modal dialog
- Session drill-down to Hands Report
- Stat column selector as a popover

Launch with:  streamlit run app/dashboard.py
"""

from __future__ import annotations

import sys
import os
import json
import time
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path so `app.*` imports work when Streamlit
# is launched from the project root.
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from app.models import SessionLocal, Hand
from app.import_service import (
    parse_uploaded_files,
    parse_directory_safe as parse_dir_service,
    run_import,
)
from app.stats import get_hero_stats, get_filter_options, ALL_POSITIONS
from app.config import HAND_HISTORY_DIR
from app.constants import CACHE_TTL
from app.prefs import get_col_setting, save_col_setting


# ── All available stat columns (in display order) ───────────────────────────
_ALL_STAT_COLS = [
    "Total Hands", "Net Won", "SD Won", "Non-SD Won", "bb/100",
    "VPIP", "PFR", "RFI", "3Bet", "4Bet", "Fold to 3Bet",
    "WTSD%", "W$SD%", "W$WSF",
    "Agg", "Postflop Agg %", "Flop CBet",
    "Bet Turn vs Missed C\u2026", "Bet River vs Missed C\u2026", "Bet Total vs Missed C\u2026",
    "Fold to BTN Steal",
    "Rake", "Rake Attr",
]

_DEFAULT_STAT_COLS = [
    "Total Hands", "Net Won", "bb/100",
    "VPIP", "PFR", "3Bet", "WTSD%", "W$SD%", "W$WSF",
    "Postflop Agg %", "Flop CBet",
    "Rake", "Rake Attr",
]


# ── Cached wrappers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=CACHE_TTL, show_spinner="Loading stats...")
def _cached_hero_stats(
    hero_name, date_from, date_to, stakes_filter, game_type_filter, position_filter,
):
    """Fetch hero stats with caching."""
    return get_hero_stats(
        hero_name=hero_name,
        date_from=date_from,
        date_to=date_to,
        stakes_filter=stakes_filter,
        game_type_filter=game_type_filter,
        position_filter=position_filter,
    )


@st.cache_data(ttl=CACHE_TTL)
def _cached_filter_options(hero_name):
    """Fetch available filter options for the hero."""
    return get_filter_options(hero_name)


@st.cache_data(ttl=CACHE_TTL)
def _cached_hand_count():
    """Return estimated hand count in DB (fast, uses pg_class stats)."""
    db = SessionLocal()
    try:
        from sqlalchemy import text as sa_text
        row = db.execute(
            sa_text("SELECT reltuples::bigint FROM pg_class WHERE relname = 'hands'")
        ).scalar()
        # reltuples can be -1 before first ANALYZE; fall back to COUNT
        if row is not None and row >= 0:
            return int(row)
        return db.query(Hand).count()
    finally:
        db.close()


# ── Import dialog ────────────────────────────────────────────────────────────

@st.dialog("📥 Import Hand Histories", width="large")
def _import_dialog():
    """Modal dialog for importing hand histories — upload files or browse a directory."""

    # session_id increments whenever the dialog is opened or after a successful
    # import, causing the file uploader key to change and its list to clear.
    session_id = st.session_state.get("_import_session", 0)

    tab_upload, tab_dir = st.tabs(["📁 Upload Files", "📂 Browse Directory"])

    with tab_upload:
        uploaded_files = st.file_uploader(
            "Drag & drop hand history files here, or click to browse",
            type=["txt"],
            accept_multiple_files=True,
            key=f"dlg_file_uploader_{session_id}",
        )
        if uploaded_files:
            st.caption(f"{len(uploaded_files)} file(s) selected")
            if st.button("Import Files", type="primary", key="dlg_import_files"):
                progress = st.progress(0, text="Parsing files…")
                total_size = sum(uf.size for uf in uploaded_files) or 1
                bytes_done = 0

                # Collect file data for the service
                file_data: list[tuple[str, bytes]] = []
                for uf in uploaded_files:
                    progress.progress(
                        bytes_done / total_size,
                        text=f"Parsing {uf.name}…",
                    )
                    file_data.append((uf.name, uf.read()))
                    bytes_done += uf.size
                    progress.progress(bytes_done / total_size)

                warnings: list[str] = []
                all_parsed = parse_uploaded_files(
                    file_data,
                    on_skip=lambda name, reason: warnings.append(
                        f"Skipping {name}: {reason}"
                    ),
                )
                progress.empty()
                for w in warnings:
                    st.warning(w)
                if all_parsed:
                    _total = len(all_parsed)
                    _imp_bar = st.progress(0, text="Importing hands… 0 / " + str(_total))

                    def _upload_cb(done, _skipped, _bar=_imp_bar, _tot=_total):
                        n = done + _skipped
                        _bar.progress(
                            min(n / _tot, 1.0),
                            text=f"Importing hands… {n:,} / {_tot:,}",
                        )

                    imported, skipped = run_import(
                        all_parsed,
                        hero_name=st.session_state.get("hero_name") or None,
                        progress_callback=_upload_cb,
                    )
                    _imp_bar.progress(1.0, text=f"Finalising…")
                    if imported > 0:
                        _cached_hero_stats.clear()
                        _cached_filter_options.clear()
                        _cached_hand_count.clear()
                    _imp_bar.empty()
                    st.success(
                        f"✓ Imported **{imported}** hands ({skipped} duplicates skipped)."
                    )
                    if imported > 0:
                        time.sleep(1)
                        st.rerun()
                else:
                    st.warning("No valid hands found in uploaded files.")

    with tab_dir:
        dir_path = st.text_input(
            "Directory path:",
            value=HAND_HISTORY_DIR,
            key="dlg_dir_path",
        )
        st.caption("All .txt files in the directory (and subdirectories) will be imported.")
        if st.button("Import from Directory", type="primary", key="dlg_import_dir"):
            parse_errors: list[str] = []
            try:
                progress = st.progress(0, text="Parsing files…")
                all_parsed = parse_dir_service(
                    dir_path,
                    on_error=lambda name: parse_errors.append(name),
                )
                progress.empty()
            except ValueError as exc:
                st.error(str(exc))
                all_parsed = []
            except FileNotFoundError as exc:
                st.error(str(exc))
                all_parsed = []

            for name in parse_errors:
                st.warning(f"Error parsing {name} — skipped.")

            if all_parsed:
                _total = len(all_parsed)
                _dir_bar = st.progress(0, text="Importing hands… 0 / " + str(_total))

                def _dir_cb(done, _skipped, _bar=_dir_bar, _tot=_total):
                    n = done + _skipped
                    _bar.progress(
                        min(n / _tot, 1.0),
                        text=f"Importing hands… {n:,} / {_tot:,}",
                    )

                imported, skipped = run_import(
                    all_parsed,
                    hero_name=st.session_state.get("hero_name") or None,
                    disable_indexes=len(all_parsed) > 5000,
                    progress_callback=_dir_cb,
                )
                _dir_bar.progress(1.0, text="Finalising…")
                if imported > 0:
                    _cached_hero_stats.clear()
                    _cached_filter_options.clear()
                    _cached_hand_count.clear()
                _dir_bar.empty()
                st.success(
                    f"✓ Imported **{imported}** hands ({skipped} duplicates skipped)."
                )
                if imported > 0:
                    time.sleep(1)
                    st.rerun()
            elif not parse_errors:
                st.warning("No valid hands found in the directory.")


# ── Purge dialog ─────────────────────────────────────────────────────────────

@st.dialog("🗑️ Purge Hands", width="large")
def _purge_dialog():
    """Modal dialog for purging hands from the database."""
    from app.data_access.hands_repository import (
        fetch_available_stakes,
        purge_hands,
    )

    hero = st.session_state.get("hero_name", "")
    if not hero:
        st.warning("No hero selected.")
        return

    avail_stakes = fetch_available_stakes(hero)
    if not avail_stakes:
        st.info("No hands in the database to purge.")
        return

    st.markdown("**Purge selected hands from the database.**")
    st.warning(
        "⚠️ This action is irreversible. Deleted hands cannot be recovered."
    )

    # ── Stakes checkboxes ────────────────────────────────────────────────────
    n_stakes = len(avail_stakes)
    purge_session = st.session_state.get("_purge_session", 0)

    # Reset checkbox state each time the dialog is freshly opened
    if st.session_state.get("_purge_dialog_session") != purge_session:
        st.session_state["_purge_dialog_session"] = purge_session
        st.session_state["purge_select_all"] = True
        for _i in range(n_stakes):
            st.session_state[f"purge_stake_{_i}"] = True

    def _on_select_all_change():
        val = st.session_state["purge_select_all"]
        for _j in range(n_stakes):
            st.session_state[f"purge_stake_{_j}"] = val

    def _on_stake_change():
        all_on = all(
            st.session_state.get(f"purge_stake_{_j}", False)
            for _j in range(n_stakes)
        )
        st.session_state["purge_select_all"] = all_on

    st.markdown("**Stakes**")
    st.checkbox("Select All", key="purge_select_all", on_change=_on_select_all_change)
    selected_stakes: list[str] = []
    cols = st.columns(min(n_stakes, 3))
    for i, label in enumerate(avail_stakes):
        # Escape $ so Streamlit doesn't interpret "$0.10/$0.20" as inline math
        display_label = label.replace("$", r"\$")
        with cols[i % len(cols)]:
            checked = st.checkbox(
                display_label, key=f"purge_stake_{i}", on_change=_on_stake_change,
            )
            if checked:
                selected_stakes.append(label)

    # Date range
    st.markdown("**Date Range**")
    d1, d2 = st.columns(2)
    with d1:
        purge_from = st.date_input(
            "From", value=None, key="purge_date_from", format="MM/DD/YYYY",
        )
    with d2:
        purge_to = st.date_input(
            "To", value=None, key="purge_date_to", format="MM/DD/YYYY",
        )

    # Action buttons
    b1, b2 = st.columns(2)
    with b1:
        purge_clicked = st.button(
            "🗑️ Purge", type="primary", key="purge_confirm",
            disabled=len(selected_stakes) == 0,
        )
    with b2:
        if st.button("Close", key="purge_close"):
            st.rerun()

    if purge_clicked and selected_stakes:
        purge_bar = st.progress(0.0, text="Starting…")

        def _purge_progress(fraction: float, text: str) -> None:
            purge_bar.progress(min(fraction, 1.0), text=text)

        deleted = purge_hands(
            hero_name=hero,
            stakes_list=selected_stakes,
            date_from=purge_from,
            date_to=purge_to,
            progress_callback=_purge_progress,
        )
        purge_bar.empty()
        if deleted > 0:
            _cached_hero_stats.clear()
            _cached_filter_options.clear()
            _cached_hand_count.clear()
            st.success(f"✓ Purged **{deleted:,}** hands.")
            time.sleep(1)
            st.rerun()
        else:
            st.info("No hands matched the selected criteria.")


# ── Shared formatters ───────────────────────────────────────────────────────
# format= is intentionally omitted here so that _apply_dataframe_style (which
# uses Python's "{:,.2f}" / "${:,.2f}" notation) stays in control of number
# formatting — including the thousands comma separator.  These entries only
# supply per-column width hints so the tables don't spread too wide.
_money_col = st.column_config.NumberColumn(width="small")
_pct_col = st.column_config.NumberColumn(width="small")
_bb100_col = st.column_config.NumberColumn(width="small")

_STAT_COL_CONFIG = {
    "Total Hands": st.column_config.NumberColumn(width="small"),
    "Net Won":    _money_col,
    "SD Won":     _money_col,
    "Non-SD Won": _money_col,
    "Rake":       _money_col,
    "Rake Attr":  _money_col,
    "bb/100":     _bb100_col,
    "VPIP":  _pct_col,
    "PFR":   _pct_col,
    "RFI":   _pct_col,
    "3Bet":  _pct_col,
    "4Bet":  _pct_col,
    "Fold to 3Bet": _pct_col,
    "WTSD%": _pct_col,
    "W$SD%": _pct_col,
    "W$WSF": _pct_col,
    "Agg":   st.column_config.NumberColumn(width="small"),
    "Postflop Agg %": _pct_col,
    "Flop CBet":  _pct_col,
    "Bet Turn vs Missed C…": _pct_col,
    "Bet River vs Missed C…": _pct_col,
    "Bet Total vs Missed C…": _pct_col,
    "Fold to BTN Steal": _pct_col,
}

_RENAME_MAP = {
    "total_hands": "Total Hands",
    "net_won":  "Net Won",
    "sd_won":   "SD Won",
    "nonsd_won": "Non-SD Won",
    "bb_per_100": "bb/100",
    "vpip":     "VPIP",
    "pfr":      "PFR",
    "rfi":      "RFI",
    "three_bet": "3Bet",
    "four_bet": "4Bet",
    "fold_to_3bet": "Fold to 3Bet",
    "wtsd_pct": "WTSD%",
    "wssd_pct": "W$SD%",
    "wwsf":     "W$WSF",
    "agg_factor": "Agg",
    "postflop_agg_pct": "Postflop Agg %",
    "flop_cbet": "Flop CBet",
    "bet_turn_vs_missed_cbet": "Bet Turn vs Missed C…",
    "bet_river_vs_missed_cbet": "Bet River vs Missed C…",
    "fold_to_btn_steal": "Fold to BTN Steal",
    "rake":     "Rake",
    "rake_attributed": "Rake Attr",
}


# ── Columns that should be summed vs. weighted-averaged ─────────────────────
_SUM_COLS = {
    "Total Hands", "Net Won", "SD Won", "Non-SD Won",
    "Rake", "Rake Attr",
}
_WEIGHTED_AVG_COLS = {
    "bb/100", "VPIP", "PFR", "RFI", "3Bet", "4Bet", "Fold to 3Bet",
    "WTSD%", "W$SD%", "W$WSF", "Agg", "Postflop Agg %", "Flop CBet",
    "Bet Turn vs Missed C\u2026", "Bet River vs Missed C\u2026",
    "Bet Total vs Missed C\u2026", "Fold to BTN Steal",
}


def _compute_summary_row(
    df: pd.DataFrame,
    label_cols: list[str],
) -> pd.DataFrame:
    """Build a single-row DataFrame with totals / weighted averages."""
    summary: dict[str, Any] = {}
    total_hands = df["Total Hands"].sum() if "Total Hands" in df.columns else 0

    for col in df.columns:
        if col in label_cols:
            summary[col] = "Total" if col == label_cols[0] else ""
        elif col in _SUM_COLS:
            summary[col] = df[col].sum()
        elif col in _WEIGHTED_AVG_COLS and total_hands > 0:
            if "Total Hands" in df.columns:
                summary[col] = round(
                    (df[col] * df["Total Hands"]).sum() / total_hands, 2
                )
            else:
                summary[col] = round(df[col].mean(), 2)
        else:
            summary[col] = ""

    return pd.DataFrame([summary])


def _apply_dataframe_style(df: pd.DataFrame) -> pd.DataFrame:
    """
    Apply pandas styling for proper comma/currency formatting.
    Works with st.dataframe() to display formatted numbers.
    """
    # Money columns (display with $ and 2 decimals)
    money_cols = {
        "Net Won", "SD Won", "Non-SD Won",
        "Rake", "Rake Attr",
    }

    # Build format dict
    format_dict = {}
    for col in df.columns:
        if col in money_cols:
            format_dict[col] = "${:,.2f}"
        elif col == "Total Hands":
            format_dict[col] = "{:,.0f}"
        elif col in {"bb/100"}:
            format_dict[col] = "{:.2f}"
        elif col in {"VPIP", "PFR", "RFI", "3Bet", "4Bet", "Fold to 3Bet",
                     "WTSD%", "W$SD%", "W$WSF", "Agg", "Postflop Agg %",
                     "Flop CBet", "Fold to BTN Steal"}:
            # Percentage columns
            if df[col].dtype in ("float64", "float32"):
                format_dict[col] = "{:.1f}"
        elif col.startswith("Bet "):
            # Bet columns (like "Bet Turn vs Missed C\u2026")
            if df[col].dtype in ("float64", "float32"):
                format_dict[col] = "{:.1f}"

    # Apply styling via pandas
    styled = df.style.format(format_dict)
    return styled


def _render_stats_table(
    df: pd.DataFrame,
    label_cols: list[str] | None = None,
    visible_stats: list[str] | None = None,
) -> None:
    """Render a stats dataframe with standard formatting and a summary row.

    *visible_stats* controls which stat columns appear; label columns are
    always shown.  Ordering follows the user's selected column order.
    A summary (totals) row is appended and rendered as a separate
    single-row table that stays visible below the main table.
    """
    if label_cols is None:
        label_cols = []
    if visible_stats is None:
        visible_stats = _DEFAULT_STAT_COLS
    # Respect the user's ordering from the multiselect
    ordered_stats = [c for c in visible_stats if c in _ALL_STAT_COLS]
    cols = label_cols + ordered_stats
    df_display = df[[c for c in cols if c in df.columns]]

    # Wrap main table + summary in a container so CSS can tighten
    # the gap between them (via .stats-table-group).
    with st.container():
        st.markdown('<div class="stats-table-group">', unsafe_allow_html=True)

        st.dataframe(
            _apply_dataframe_style(df_display),
            column_config=_STAT_COL_CONFIG,
            width='stretch',
            height=min(len(df_display) * 40 + 40, 600),
        )

        # Render summary row as a fixed single-row table below
        if len(df_display) > 0:
            summary = _compute_summary_row(df_display, label_cols)
            st.dataframe(
                _apply_dataframe_style(summary),
                column_config=_STAT_COL_CONFIG,
                width='stretch',
                height=76,
                hide_index=True,
            )

        st.markdown('</div>', unsafe_allow_html=True)


def _render_cumulative_chart(cum_data: list[dict]) -> None:
    """Render the cumulative P&L line chart."""
    if not cum_data:
        st.write("No data to display.")
        return

    df_cum = pd.DataFrame(cum_data)
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=df_cum["hand_num"], y=df_cum["nonsd_won_cumulative"],
        mode="lines", name="Non-SD Winnings",
        line=dict(color="#e74c3c", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df_cum["hand_num"], y=df_cum["sd_won_cumulative"],
        mode="lines", name="SD Winnings",
        line=dict(color="#1f77b4", width=1.5),
    ))
    fig.add_trace(go.Scatter(
        x=df_cum["hand_num"], y=df_cum["net_won_cumulative"],
        mode="lines", name="Net Won",
        line=dict(color="#2ca02c", width=2.5),
    ))

    fig.add_hline(y=0, line_dash="dash", line_color="grey", opacity=0.5)
    fig.update_layout(
        hovermode="x unified",
        yaxis_tickprefix="$",
        xaxis_title="Hand #",
        yaxis_title="Cumulative ($)",
        template="plotly_dark",
        height=400,
        margin=dict(t=30, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width='stretch')


def _render_bar_chart(
    data: list[dict],
    category_key: str,
    title: str,
    colors: dict[str, str] | None = None,
) -> None:
    """Render a grouped bar chart of Net Won / SD Won / Non-SD Won per category."""
    if not data:
        st.write("No data to display.")
        return

    df = pd.DataFrame(data)
    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df[category_key], y=df["net_won"],
        name="Net Won",
        marker_color=colors.get("net", "#2ca02c") if colors else "#2ca02c",
    ))
    fig.add_trace(go.Bar(
        x=df[category_key], y=df["sd_won"],
        name="SD Won",
        marker_color=colors.get("sd", "#d62728") if colors else "#d62728",
    ))
    fig.add_trace(go.Bar(
        x=df[category_key], y=df["nonsd_won"],
        name="Non-SD Won",
        marker_color=colors.get("nonsd", "#1f77b4") if colors else "#1f77b4",
    ))

    fig.add_hline(y=0, line_dash="dash", line_color="grey", opacity=0.5)
    fig.update_layout(
        barmode="group",
        hovermode="x unified",
        yaxis_tickprefix="$",
        xaxis_title=title,
        yaxis_title="Winnings ($)",
        template="plotly_dark",
        height=350,
        margin=dict(t=30, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, width='stretch')


# ── Main content ─────────────────────────────────────────────────────────────
_hero = st.session_state.get("hero_name", "")

total_in_db = _cached_hand_count()

# ── Top toolbar: Import button + Filter bar ──────────────────────────────────
# Header row with title and import button

hdr_left, hdr_imp, hdr_purge = st.columns([6, 1, 1], gap="small")
with hdr_left:
    st.markdown("### 📊 Dashboard")
with hdr_imp:
    if st.button("📥 Import", use_container_width=True, type="secondary"):
        # Increment session ID each time the dialog is opened so the file
        # uploader starts fresh (clearing any previously staged files).
        st.session_state["_import_session"] = (
            st.session_state.get("_import_session", 0) + 1
        )
        _import_dialog()
with hdr_purge:
    if st.button("🗑️ Purge", use_container_width=True, type="secondary"):
        st.session_state["_purge_session"] = (
            st.session_state.get("_purge_session", 0) + 1
        )
        _purge_dialog()

# Get filter options from DB
avail_stakes, avail_game_types = _cached_filter_options(_hero)

# Compact horizontal filter bar
with st.container():
    fc1, fc2, fc3, fc4, fc5 = st.columns([1, 1, 2, 2, 2], gap="small")
    with fc1:
        date_from = st.date_input(
            "From", value=None, key="dash_date_from", format="MM/DD/YYYY",
        )
    with fc2:
        date_to = st.date_input(
            "To", value=None, key="dash_date_to", format="MM/DD/YYYY",
        )
    with fc3:
        stakes_filter = st.multiselect(
            "Stakes", options=avail_stakes, default=[],
            placeholder="All stakes", key="dash_stakes",
        )
    with fc4:
        game_type_filter = st.multiselect(
            "Game Type", options=avail_game_types, default=[],
            placeholder="All game types", key="dash_game_type",
        )
    with fc5:
        position_filter = st.multiselect(
            "Position", options=ALL_POSITIONS, default=[],
            placeholder="All positions", key="dash_position",
        )


if total_in_db == 0:
    st.markdown("---")
    st.markdown("## 👋 Welcome to BetRivers Poker Tracker - Unofficial!")
    st.info(
        "No hand histories have been imported yet. "
        "Import your BetRivers hand history `.txt` files to get started."
    )
    if st.button("📥 Import Hand Histories", type="primary", use_container_width=False):
        st.session_state["_import_session"] = (
            st.session_state.get("_import_session", 0) + 1
        )
        _import_dialog()
    st.markdown(
        "**Tip:** Your BetRivers hand history files are usually found in your "
        "Downloads folder or wherever the BetRivers client saves them. "
        "You can drag and drop multiple `.txt` files at once."
    )
    st.stop()

# Fetch stats (with filters) — cached for identical filter combos
_sf = tuple(stakes_filter) if stakes_filter else None
_gf = tuple(game_type_filter) if game_type_filter else None
_pf = tuple(position_filter) if position_filter else None

session_stats, cumulative, by_stakes, by_position = _cached_hero_stats(
    hero_name=_hero,
    date_from=date_from if date_from else None,
    date_to=date_to if date_to else None,
    stakes_filter=list(_sf) if _sf else None,
    game_type_filter=list(_gf) if _gf else None,
    position_filter=list(_pf) if _pf else None,
)

# ── KPI row ──────────────────────────────────────────────────────────────────
if session_stats:
    total_hands = sum(s["total_hands"] for s in session_stats)
    total_net = sum(s["net_won"] for s in session_stats)
    total_sd = sum(s["sd_won"] for s in session_stats)
    total_nonsd = sum(s["nonsd_won"] for s in session_stats)
    total_rake = sum(s["rake_attributed"] for s in session_stats)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total Hands", f"{total_hands:,}")
    k2.metric("Net Won", f"${total_net:,.2f}")
    k3.metric("SD Winnings", f"${total_sd:,.2f}")
    k4.metric("Non-SD Winnings", f"${total_nonsd:,.2f}")
    k5.metric("Rake Attributed", f"${total_rake:,.2f}")


# ── Tabbed reports ───────────────────────────────────────────────────────────
tab_overview, tab_stakes, tab_position = st.tabs([
    "📊  Session Report",
    "💰  Results by Stakes",
    "🎯  Results by Position",
])


# ── Tab 1: Session Report (Overview) ────────────────────────────────────────
with tab_overview:
    _render_cumulative_chart(cumulative)

    # ── Column selector — rendered unconditionally so Streamlit never drops
    # the session state key when there is temporarily no data to display.
    with st.popover("⚙ Columns"):
        if st.button("↩ Reset", key="reset_cols_session"):
            st.session_state["vis_cols_session"] = list(_DEFAULT_STAT_COLS)
            save_col_setting("cols_session", list(_DEFAULT_STAT_COLS))
        st.multiselect(
            "Visible stats",
            options=_ALL_STAT_COLS,
            default=get_col_setting("cols_session", _DEFAULT_STAT_COLS),
            key="vis_cols_session",
            label_visibility="collapsed",
            on_change=lambda: save_col_setting(
                "cols_session",
                st.session_state.get("vis_cols_session", []),
            ),
        )
    # `or` guards against an empty list (e.g. user cleared all selections)
    _vis_session = st.session_state.get("vis_cols_session") or _DEFAULT_STAT_COLS

    if session_stats:
        df_sess = pd.DataFrame(session_stats)

        # Preserve raw date for navigation
        df_sess["_raw_date"] = df_sess["hand_date"]

        df_sess["hand_date"] = pd.to_datetime(
            df_sess["hand_date"]
        ).dt.strftime("%Y-%m-%d")
        df_sess["session_start"] = pd.to_datetime(
            df_sess["session_start"]
        ).dt.strftime("%I:%M %p")
        df_sess = df_sess.rename(columns={
            **_RENAME_MAP,
            "hand_date": "Date",
            "session_start": "Start",
        })

        ordered_stats = [c for c in _vis_session if c in _ALL_STAT_COLS]
        cols = ["Date", "Start"] + ordered_stats
        df_show = df_sess[[c for c in cols if c in df_sess.columns]].copy()

        # Session table with row selection for drill-down
        selection = st.dataframe(
            _apply_dataframe_style(df_show),
            column_config=_STAT_COL_CONFIG,
            hide_index=True,
            width='stretch',
            height=min(len(df_show) * 40 + 40, 450),
            on_select="rerun",
            selection_mode="multi-row",
            key="session_grid",
        )

        # Summary row below the session table
        if len(df_show) > 0:
            summary = _compute_summary_row(df_show, ["Date", "Start"])
            st.dataframe(
                _apply_dataframe_style(summary),
                column_config=_STAT_COL_CONFIG,
                width='stretch',
                height=76,
                hide_index=True,
            )

        _bot_left, bot_right = st.columns([1, 1])
        with bot_right:
            # Drill-down: navigate to Hands Report for selected session(s)
            selected_rows = (
                selection.selection.rows
                if selection and selection.selection
                else []
            )
            n_sel = len(selected_rows)
            btn_disabled = n_sel == 0
            btn_label = (
                f"📋 View Hands for Sessions ({n_sel})" if n_sel > 1
                else "📋 View Hands for Session"
            )
            if st.button(
                btn_label,
                disabled=btn_disabled,
                type="primary",
                use_container_width=True,
            ):
                if selected_rows:
                    raw_dates = sorted(
                        df_sess.iloc[i]["_raw_date"] for i in selected_rows
                    )
                    if len(raw_dates) == 1:
                        st.session_state["hr_prefill_session_date"] = raw_dates[0]
                    else:
                        st.session_state["hr_prefill_session_dates"] = raw_dates
                    try:
                        st.switch_page("hands_report.py")
                    except Exception:
                        st.info("Navigate to **Hands Report** to view.")
    else:
        st.write("No session data for the selected filters.")


# ── Tab 2: Results by Stakes ─────────────────────────────────────────────────
with tab_stakes:
    with st.popover("⚙ Columns"):
        if st.button("↩ Reset", key="reset_cols_stakes"):
            st.session_state["vis_cols_stakes"] = list(_DEFAULT_STAT_COLS)
            save_col_setting("cols_stakes", list(_DEFAULT_STAT_COLS))
        st.multiselect(
            "Visible stats",
            options=_ALL_STAT_COLS,
            default=get_col_setting("cols_stakes", _DEFAULT_STAT_COLS),
            key="vis_cols_stakes",
            label_visibility="collapsed",
            on_change=lambda: save_col_setting(
                "cols_stakes",
                st.session_state.get("vis_cols_stakes", []),
            ),
        )
    _vis_stakes = st.session_state.get("vis_cols_stakes") or _DEFAULT_STAT_COLS

    if by_stakes:
        _render_bar_chart(by_stakes, "stakes", "Stakes Level")

        df_st = pd.DataFrame(by_stakes)
        t_col = "bet_turn_vs_missed_cbet"
        r_col = "bet_river_vs_missed_cbet"
        if t_col in df_st.columns and r_col in df_st.columns:
            df_st["bet_total_vs_missed_cbet"] = df_st.apply(
                lambda row: round(
                    (row[t_col] + row[r_col]) / 2, 1
                ) if row[t_col] or row[r_col] else 0.0,
                axis=1,
            )
        df_st = df_st.rename(columns={
            **_RENAME_MAP,
            "stakes": "Stakes",
            "seats": "Seats",
            "bet_total_vs_missed_cbet": "Bet Total vs Missed C\u2026",
        })
        _render_stats_table(
            df_st,
            label_cols=["Stakes", "Seats"],
            visible_stats=_vis_stakes,
        )
    else:
        st.write("No data for the selected filters.")


# ── Tab 3: Results by Position ───────────────────────────────────────────────
with tab_position:
    with st.popover("⚙ Columns"):
        if st.button("↩ Reset", key="reset_cols_position"):
            st.session_state["vis_cols_position"] = list(_DEFAULT_STAT_COLS)
            save_col_setting("cols_position", list(_DEFAULT_STAT_COLS))
        st.multiselect(
            "Visible stats",
            options=_ALL_STAT_COLS,
            default=get_col_setting("cols_position", _DEFAULT_STAT_COLS),
            key="vis_cols_position",
            label_visibility="collapsed",
            on_change=lambda: save_col_setting(
                "cols_position",
                st.session_state.get("vis_cols_position", []),
            ),
        )
    _vis_position = st.session_state.get("vis_cols_position") or _DEFAULT_STAT_COLS

    if by_position:
        _render_bar_chart(by_position, "position", "Position")

        df_pos = pd.DataFrame(by_position)
        df_pos = df_pos.rename(columns={**_RENAME_MAP, "position": "Position"})
        _render_stats_table(
            df_pos,
            label_cols=["Position"],
            visible_stats=_vis_position,
        )
    else:
        st.write("No data for the selected filters.")
