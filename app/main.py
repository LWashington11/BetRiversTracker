"""
BetRivers Poker Tracker — Multi-page Streamlit app.

Multi-page layout with:
- Compact sidebar (hero selector + navigation only)
- Filters in the main content area
- Import as a modal dialog

Launch with:  streamlit run app/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import streamlit as st
from app.constants import DONATION_URL, GITHUB_RELEASES_URL
from app.models import init_db
from app.hero_store import get_hero_names, get_last_hero, save_hero
from app.ui.styles import inject_responsive_css

# ── Page config (must be first Streamlit call) ───────────────────────────────
# Expand the sidebar on first run (no hero stored yet) so the
# hero-name prompt is immediately visible; collapse it afterwards.
_first_run = "_app_initialized" not in st.session_state
st.set_page_config(
    page_title="BetRivers Poker Tracker",
    page_icon="♠",
    layout="wide",
    initial_sidebar_state="expanded" if _first_run else "collapsed",
)
if _first_run:
    st.session_state["_app_initialized"] = True

# ── Inject responsive CSS (once per page load) ──────────────────────────────
inject_responsive_css()

# ── Hide deploy button ───────────────────────────────────────────────────────
st.markdown("""
    <style>
    .stAppDeployButton {display: none;}
    #MainMenu {display: none;}
    .stToolbarActions {display: none;}
    [data-testid="stStatusWidget"] {display: none;}
    </style>
    """, unsafe_allow_html=True)

# ── Ensure DB tables exist (once per session, not on every rerun) ───────────
if "_db_initialized" not in st.session_state:
    init_db()
    st.session_state["_db_initialized"] = True

# ── Hero selector (shared across all pages via session_state) ────────────────
_stored_names = get_hero_names()
_last = get_last_hero()

# Initialize session state
if "hero_name" not in st.session_state:
    st.session_state.hero_name = _last or ""

# If a new hero was just saved on the previous run, clear the stale widget
# keys BEFORE the widgets are instantiated so the selectbox falls back to
# the computed _default_idx rather than the persisted "_ADD_NEW" value.
if st.session_state.pop("_new_hero_saved", False):
    st.session_state.pop("_hero_select", None)
    st.session_state.pop("_hero_new_input", None)

st.sidebar.header("♠ Hero")

# Combobox: selectbox of known names + free-text entry
_options = _stored_names if _stored_names else []
_ADD_NEW = "✎ Enter a new name…"

_choices = _options + [_ADD_NEW]
_current = st.session_state.hero_name
_default_idx = 0
if _current in _options:
    _default_idx = _options.index(_current)
elif _current and _options:
    # Current name not in list — was just typed; prepend it
    _choices = [_current] + _options + [_ADD_NEW]
    _default_idx = 0

_selected = st.sidebar.selectbox(
    "Hero Player",
    options=_choices,
    index=_default_idx,
    key="_hero_select",
)

if _selected == _ADD_NEW:
    _new_name = st.sidebar.text_input("New hero name:", key="_hero_new_input")
    if _new_name and _new_name.strip():
        _new_name = _new_name.strip()
        save_hero(_new_name)
        st.session_state.hero_name = _new_name
        # Set a plain (non-widget) flag so the top of the next rerun can
        # clear the stale widget keys before they are instantiated.
        st.session_state["_new_hero_saved"] = True
        st.rerun()
elif _selected:
    if _selected != st.session_state.hero_name:
        save_hero(_selected)
        st.session_state.hero_name = _selected
        st.rerun()

if not st.session_state.hero_name:
    st.sidebar.warning("Set a hero name above to get started.")

    # ── Welcome / onboarding screen ──────────────────────────────────
    # Welcome-box CSS is in responsive.css (injected above).
    st.markdown(
        """
        <div class="welcome-box">
            <h1>♠ BetRivers Poker Tracker</h1>
            <p>Track your results, review hand histories, and replay key hands.</p>
            <hr style="margin: 1.25rem 0;">
            <div class="welcome-step">
                <span class="arrow-hint">① </span>
                Click the <strong>≫</strong> arrow on the top-left to open the sidebar.
            </div>
            <div class="welcome-step">
                <span class="arrow-hint">② </span>
                Under <strong>♠ Hero</strong>, select <em>✎ Enter a new name…</em>
                then type your screen name and press enter.
            </div>
            <div class="welcome-step">
                <span class="arrow-hint">③ </span>
                Use <strong>File → Import</strong> to load your hand history files.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()

st.sidebar.caption(f"Hero: **{st.session_state.hero_name}**")
st.sidebar.markdown("---")

# ── Support Development ──────────────────────────────────────────────────────
if DONATION_URL:
    with st.sidebar.expander("☕ Support Development", expanded=False):
        st.write(
            "If you find this tool useful, consider supporting its"
            " development:"
        )
        st.link_button(
            "☕ Buy me a coffee",
            url=DONATION_URL,
            use_container_width=True,
        )

st.sidebar.link_button(
    "🔄 Check for Updates",
    url=GITHUB_RELEASES_URL,
    use_container_width=True,
)

st.sidebar.markdown("---")


@st.dialog("Confirm Quit")
def _confirm_quit():
    st.write("Are you sure you want to quit the app?")
    col_yes, col_no = st.columns(2)
    if col_yes.button("Yes, Quit", type="primary", use_container_width=True):
        import threading
        threading.Timer(1.5, lambda: sys.exit(0)).start()
        st.components.v1.html(
            "<script>window.top.close();</script>", height=0,
        )
        st.info("The app is shutting down… you may close this tab.")
        st.stop()
    if col_no.button("Cancel", use_container_width=True):
        st.rerun()


if st.sidebar.button("⏹ Quit App", use_container_width=True, type="secondary"):
    _confirm_quit()

# ── Navigation ───────────────────────────────────────────────────────────────
pages = [
    st.Page("dashboard.py", title="Dashboard", icon="📊", default=True),
    st.Page("hands_report.py", title="Hands Report", icon="📋"),
    st.Page("replayer.py", title="Hand Replayer", icon="🃏"),
]

pg = st.navigation(pages)
pg.run()
