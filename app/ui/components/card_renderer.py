"""
Card Renderer — CSS/HTML card rendering and AG Grid JsCode cell renderers.

Provides:
•  Python-side HTML helpers for rendering cards outside the grid.
•  JavaScript cell-renderer strings for AG Grid (used via ``JsCode``).
•  A self-contained CSS block for the HTML-fallback table.

Suit colour scheme (4-colour deck):
    ♥ Hearts   — red    (#e74c3c)
    ♦ Diamonds — blue   (#3498db)
    ♣ Clubs    — green  (#27ae60)
    ♠ Spades   — dark   (#2c3e50)

Cards are rendered as compact inline elements with a white background
so they remain legible on both light and dark Streamlit themes.
"""

from __future__ import annotations


# ── Suit maps ────────────────────────────────────────────────────────────────

SUIT_SYMBOL = {"h": "♥", "d": "♦", "c": "♣", "s": "♠"}
SUIT_COLOR = {"h": "#e74c3c", "d": "#3498db", "c": "#27ae60", "s": "#2c3e50"}


# ── Python-side HTML helpers ─────────────────────────────────────────────────

def render_card_html(card: str) -> str:
    """
    Render a single card (e.g. ``'Kh'``) as an HTML ``<span>``.

    Returns an empty string for invalid input.
    """
    if not card or len(card) < 2:
        return ""
    rank = card[:-1]
    suit = card[-1].lower()
    sym = SUIT_SYMBOL.get(suit, "?")
    color = SUIT_COLOR.get(suit, "#333")
    return (
        f'<span style="display:inline-block;background:#f8f9fa;border:1px solid #dee2e6;'
        f'border-radius:3px;padding:0 3px;margin:0 1px;font-weight:700;font-size:11px;'
        f'line-height:20px;color:{color};font-family:monospace;">'
        f'{rank}<span style="font-size:10px;">{sym}</span></span>'
    )


def render_cards_html(cards_str: str) -> str:
    """
    Render a space-separated card string (e.g. ``'8d Ks'``) as inline HTML.
    """
    if not cards_str or not cards_str.strip():
        return '<span style="color:#666;">-</span>'
    cards = cards_str.strip().split()
    return (
        '<div style="display:inline-flex;gap:2px;align-items:center;">'
        + "".join(render_card_html(c) for c in cards)
        + "</div>"
    )


# ── AG Grid JsCode cell renderers (string form) ─────────────────────────────
# These are passed to ``JsCode(...)`` in the view layer.

AGGRID_CARD_RENDERER_JS = """
function(params) {
    if (!params.value) return '';
    var cards = params.value.trim().split(/\\s+/);
    var suitMap = {'h':'\\u2665','d':'\\u2666','c':'\\u2663','s':'\\u2660'};
    var colorMap = {'h':'#e74c3c','d':'#3498db','c':'#27ae60','s':'#2c3e50'};
    var html = '<div style="display:flex;gap:2px;align-items:center;height:100%;">';
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var rank = c.slice(0, -1);
        var suit = c.slice(-1).toLowerCase();
        var sym = suitMap[suit] || '?';
        var color = colorMap[suit] || '#333';
        html += '<span style="display:inline-block;background:#f8f9fa;border:1px solid #dee2e6;'
              + 'border-radius:3px;padding:0 3px;font-weight:700;font-size:11px;'
              + 'line-height:20px;color:' + color + ';font-family:monospace;">'
              + rank + '<span style="font-size:10px;">' + sym + '</span></span>';
    }
    html += '</div>';
    return html;
}
"""

AGGRID_BOARD_RENDERER_JS = """
function(params) {
    if (!params.value) return '<span style="color:#666;">-</span>';
    var cards = params.value.trim().split(/\\s+/);
    var suitMap = {'h':'\\u2665','d':'\\u2666','c':'\\u2663','s':'\\u2660'};
    var colorMap = {'h':'#e74c3c','d':'#3498db','c':'#27ae60','s':'#2c3e50'};
    var html = '<div style="display:flex;gap:2px;align-items:center;height:100%;">';
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        var rank = c.slice(0, -1);
        var suit = c.slice(-1).toLowerCase();
        var sym = suitMap[suit] || '?';
        var color = colorMap[suit] || '#333';
        // visual gap between flop and turn/river
        if (i === 3 || i === 4) {
            html += '<span style="width:3px;"></span>';
        }
        html += '<span style="display:inline-block;background:#f8f9fa;border:1px solid #dee2e6;'
              + 'border-radius:3px;padding:0 3px;font-weight:700;font-size:11px;'
              + 'line-height:20px;color:' + color + ';font-family:monospace;">'
              + rank + '<span style="font-size:10px;">' + sym + '</span></span>';
    }
    html += '</div>';
    return html;
}
"""

AGGRID_PROFIT_RENDERER_JS = """
function(params) {
    var val = params.value;
    if (val === null || val === undefined || val === '' || val === 0) {
        return '<span style="color:#888;">0.00</span>';
    }
    val = parseFloat(val);
    if (isNaN(val)) return '';
    var color = val > 0 ? '#2ecc71' : val < 0 ? '#e74c3c' : '#888';
    var prefix = val > 0 ? '+' : '';
    return '<span style="color:' + color + ';font-weight:600;">'
         + prefix + val.toFixed(2) + '</span>';
}
"""


# ── CSS for the HTML-fallback table ──────────────────────────────────────────

FALLBACK_TABLE_CSS = """
<style>
.hir-table-wrap {
    max-height: 640px;
    overflow-y: auto;
    border: 1px solid #333;
    border-radius: 4px;
}
.hir-table {
    width: 100%;
    border-collapse: collapse;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 12px;
}
.hir-table thead {
    position: sticky;
    top: 0;
    z-index: 10;
}
.hir-table th {
    background: #1a1a2e;
    color: #e0e0e0;
    padding: 6px 8px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    white-space: nowrap;
    border-bottom: 2px solid #444;
    cursor: pointer;
    user-select: none;
}
.hir-table th:hover {
    background: #262640;
}
.hir-table th .sort-arrow {
    margin-left: 4px;
    font-size: 10px;
    color: #888;
}
.hir-table td {
    padding: 4px 8px;
    white-space: nowrap;
    border-bottom: 1px solid #2a2a3e;
    color: #ddd;
    vertical-align: middle;
}
.hir-table tbody tr:hover {
    background: #262640;
}
.hir-table tbody tr.selected {
    background: #1a3a5c !important;
}
.hir-table tbody tr:nth-child(even) {
    background: #16162a;
}
.hir-table tbody tr:nth-child(odd) {
    background: #111122;
}
.profit-pos { color: #2ecc71; font-weight: 600; }
.profit-neg { color: #e74c3c; font-weight: 600; }
.profit-zero { color: #888; }
</style>
"""
