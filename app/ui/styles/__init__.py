"""
Responsive CSS injection for Streamlit.

Call ``inject_responsive_css()`` once per page load (typically right
after ``st.set_page_config()``) to apply the shared responsive
stylesheet to every page of the multi-page app.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import streamlit as st

_CSS_PATH = Path(__file__).with_name("responsive.css")


@lru_cache(maxsize=1)
def _read_css() -> str:
    """Read the CSS file once and cache the result in-process."""
    return _CSS_PATH.read_text(encoding="utf-8")


def inject_responsive_css() -> None:
    """Inject the responsive stylesheet into the current Streamlit page."""
    css = _read_css()
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
