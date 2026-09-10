"""
QueryAnalyzerDialog
────────────────────
Consolidated query-analysis dialog — combines the pre-run cost estimate
and post-run profile (services/query_cost.py — lifted from the standalone
query_analyzer.py CLI so both share one rule engine) with the existing
query-vs-query Compare tool (services/query_verifier.py) in one place, so
"is this query expensive" and "do these two queries return the same data"
are one entry point instead of two easily-confused ones.

Opened from the Database menu (main.py) via
ConnectionPanel.open_query_analyzer() — replaces the old per-tab "Verify"
button.
"""

from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services import query_cost
from services.query_verifier import QueryVerifier, VerifyResult
from ui.code_editor import CodeEditor
from ui.sql_highlighter import SqlHighlighter

# ─── Shared palette (used by both tabs) ────────────────────────────────────

_PASS_COLOR = "#30d158"   # macOS green  # nosec B105
_FAIL_COLOR = "#ff453a"   # macOS red
_WARN_COLOR = "#ff9f0a"   # macOS orange
_INFO_COLOR = "#0A84FF"
_MUTED      = "#8e8e93"
_BORDER     = "#3a3a3c"
_PANEL_BG   = "#2c2c2e"
_BG         = "#1c1c1e"
_TEXT       = "#e5e5ea"

def _alpha(hex_color: str, alpha_hex: str) -> str:
    """A translucent tint of hex_color. Qt Style Sheets (and QColor's own
    string parser) read an 8-digit hex color as #AARRGGBB — alpha *first*,
    not the CSS convention of alpha last — so the alpha byte has to be
    prepended, not appended, or the "tint" comes out as an unrelated hue."""
    return f"#{alpha_hex}{hex_color.lstrip('#')}"


_SEV_COLOR = {
    "CRITICAL": _FAIL_COLOR, "HIGH": _FAIL_COLOR,
    "MEDIUM": _WARN_COLOR, "LOW": _INFO_COLOR, "INFO": _MUTED,
}

# Status word + color for a _check_row — text badges instead of emoji, per
# the "clean professional IDE aesthetic" requirement (no colored icons as
# the sole way to communicate meaning).
_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "warn": "WARNING", "info": "INFO"}
_STATUS_COLOR = {
    "pass": _PASS_COLOR, "fail": _FAIL_COLOR, "warn": _WARN_COLOR, "info": _INFO_COLOR,
}


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{_MUTED}; font-size:11px; font-weight:600;"
        f" text-transform:uppercase; letter-spacing:0.5px;"
        f" padding:6px 0 2px 0;"
    )
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color:{_BORDER}; background:{_BORDER}; max-height:1px;")
    return line


def _text_chip(text: str, color: str, width: int = 66) -> QLabel:
    """A small colored border/background text badge — the "severity badges"
    called for instead of colored emoji icons."""
    chip = QLabel(text)
    chip.setFixedWidth(width)
    chip.setAlignment(Qt.AlignCenter)
    chip.setStyleSheet(
        f"color:{color}; font-size:10px; font-weight:700; letter-spacing:0.5px;"
        f" border:1px solid {_alpha(color, '66')}; border-radius:3px; padding:2px 4px;"
        f" background:{_alpha(color, '14')};"
    )
    return chip


def _status_chip(status: str) -> QLabel:
    return _text_chip(_STATUS_LABEL[status], _STATUS_COLOR[status])


def _check_row(status: str | None, message: str, sub: str = "") -> QWidget:
    """One row of a results report: a PASS/FAIL/WARNING/INFO text badge
    (status=None for a plain bullet with no badge) + main message + optional
    subtext."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(8)

    if status is not None:
        chip = _status_chip(status)
        chip.setAlignment(Qt.AlignCenter | Qt.AlignTop)
        h.addWidget(chip, 0, Qt.AlignTop)

    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(1)

    main_lbl = QLabel(message)
    main_lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
    main_lbl.setWordWrap(True)
    main_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    body.addWidget(main_lbl)

    if sub:
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        sub_lbl.setWordWrap(True)
        sub_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        body.addWidget(sub_lbl)

    h.addLayout(body, 1)
    return w


def _score_color(score: int) -> str:
    if score == 0:
        return _PASS_COLOR
    if score < 20:
        return _INFO_COLOR
    if score < 50:
        return _WARN_COLOR
    return _FAIL_COLOR


_RISK_COLOR = {
    "Low": _PASS_COLOR, "Medium": _WARN_COLOR,
    "High": _FAIL_COLOR, "Critical": _FAIL_COLOR,
}

_DIALECT_COST_LABEL = {
    "mysql": "MySQL Optimizer Cost", "postgresql": "PostgreSQL Planner Cost",
}

_DIALECT_COST_TOOLTIP = {
    "mysql": (
        "MySQL Optimizer Cost is a relative, unitless cost the MySQL "
        "optimizer calculates when choosing between execution plans. It "
        "is NOT milliseconds, execution time, CPU time, monetary cost, or "
        "a percentage — a cost of 40 is not \"40 ms\"."
    ),
    "postgresql": (
        "PostgreSQL Planner Cost is a relative, unitless cost the "
        "PostgreSQL planner calculates when choosing between execution "
        "plans. It is NOT milliseconds, execution time, CPU time, "
        "monetary cost, or a percentage."
    ),
}

_ROWS_EXAMINED_TOOLTIP = (
    "Rows the optimizer expects to read while executing this plan, per "
    "EXPLAIN — not the number of rows the query returns to you."
)


def _na(value, fmt: str = "{}") -> str:
    """Render a possibly-unavailable metric — never fabricate a number when
    the dialect/plan simply doesn't expose one."""
    return fmt.format(value) if value is not None else "N/A"


def _format_duration(seconds: float | None) -> str:
    """Human-readable, unit-aware duration — chooses ms vs s automatically
    so a ~100ms query never renders as a misleading "0.11s"."""
    if seconds is None:
        return "N/A"
    ms = seconds * 1000
    if ms >= 1000:
        return f"{seconds:.2f} s"
    if ms >= 10:
        return f"{ms:.0f} ms"
    return f"{ms:.2f} ms"


def _format_signed_duration_ms(ms_diff: float) -> str:
    sign = "+" if ms_diff >= 0 else "-"
    return f"{sign}{_format_duration(abs(ms_diff) / 1000.0)}"


def _stat_card(label: str, value: str, color: str = None, sub: str = "",
                tooltip: str = "") -> QWidget:
    """One compact tile in the Performance Summary row."""
    card = QFrame()
    card.setStyleSheet(
        f"background:{_PANEL_BG}; border:1px solid {_BORDER}; border-radius:6px;"
    )
    if tooltip:
        card.setToolTip(tooltip)
    v = QVBoxLayout(card)
    v.setContentsMargins(12, 8, 12, 8)
    v.setSpacing(2)

    lbl = QLabel(label.upper())
    lbl.setStyleSheet(
        f"color:{_MUTED}; font-size:10px; font-weight:600; letter-spacing:0.5px; border:none;"
    )
    v.addWidget(lbl)

    val_lbl = QLabel(value)
    val_lbl.setStyleSheet(f"color:{color or _TEXT}; font-size:16px; font-weight:700; border:none;")
    val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    v.addWidget(val_lbl)

    if sub:
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet(f"color:{_MUTED}; font-size:10px; border:none;")
        sub_lbl.setWordWrap(True)
        v.addWidget(sub_lbl)

    return card


def _summary_row(cards: list) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    for c in cards:
        h.addWidget(c, 1)
    return w


def _mode_badge(text: str, color: str) -> QWidget:
    """The small "ESTIMATE — QUERY NOT EXECUTED" / "PROFILE — QUERY EXECUTED"
    strip under a Performance Summary heading — makes it explicit whether
    the numbers below came from a plan-only EXPLAIN or a real execution."""
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{color}; font-size:10px; font-weight:700; letter-spacing:0.5px;"
        f" padding:0 0 4px 0;"
    )
    return lbl


def _copy_with_feedback(btn: QPushButton, text: str, original_label: str) -> None:
    """Copy `text` to the clipboard and briefly swap the button's label to
    a confirmation — a clipboard write is otherwise silent, so without this
    there's no way to tell the click actually registered."""
    QApplication.clipboard().setText(text)
    btn.setText("✓ Copied")

    def _revert():
        try:
            btn.setText(original_label)
        except RuntimeError:
            pass  # the widget (dialog/tab) was already closed

    QTimer.singleShot(1200, _revert)


def _kv_row(label: str, value: str, value2: str = None, color: str = None) -> QWidget:
    """A label + one or two right-aligned-ish values on one line — used by
    the Compare Queries Performance / Plan Comparison sections. Pass value2
    to render "Query 1: X   Query 2: Y" from a single call."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 1, 0, 1)
    h.setSpacing(8)

    lbl = QLabel(label)
    lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px; font-weight:600;")
    lbl.setFixedWidth(100)
    h.addWidget(lbl, 0, Qt.AlignTop)

    if value2 is not None:
        text = f"Query 1: {value}      Query 2: {value2}"
    else:
        text = value
    val = QLabel(text)
    val.setStyleSheet(f"color:{color or _TEXT}; font-size:12px; font-weight:600;")
    val.setWordWrap(True)
    val.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    h.addWidget(val, 1)
    return w


# MySQL access-type ("type" column of EXPLAIN) mapped to a plain-language
# label — used to describe a plan-access-method change (Part 13) without
# claiming it's "better", only naming what MySQL itself reports changed.
_ACCESS_TYPE_LABEL = {
    "system": "Constant Lookup", "const": "Constant Lookup",
    "eq_ref": "Unique Index Lookup", "ref": "Index Lookup",
    "range": "Range Scan", "index_merge": "Index Merge",
    "ref_or_null": "Index Lookup (Ref or Null)",
    "index": "Full Index Scan", "ALL": "Full Table Scan",
}


def _access_type_label(access_type: str) -> str:
    return _ACCESS_TYPE_LABEL.get(access_type, access_type or "Unknown")


def _issues_by_table(issues: list) -> dict:
    """Worst-severity scan-related issue per table (lowercased), so the
    Execution Plan can be colored by the exact same verdict already shown
    in the Issues section instead of forming a second, disconnected
    opinion about which nodes are "bad"."""
    by_table: dict = {}
    for issue in issues or []:
        if issue.code not in ("FULL_TABLE_SCAN", "NO_POSSIBLE_KEYS"):
            continue
        m = re.search(r"Table `([^`]+)`", issue.message)
        if not m:
            continue
        tbl = m.group(1).lower()
        cur = by_table.get(tbl)
        if cur is None or query_cost.SEVERITY_SCORE[issue.severity] > query_cost.SEVERITY_SCORE[cur.severity]:
            by_table[tbl] = issue
    return by_table


def _plan_explain_bits(node) -> list:
    """Access type / possible keys / key / filtered — sourced only from
    values EXPLAIN actually reported for this node's table (see
    services.query_cost._annotate_tree_from_explain_rows). A field EXPLAIN
    didn't report is simply omitted, never invented."""
    bits = []
    if node.access_type:
        bits.append(f"access: {_access_type_label(node.access_type)}")
    if node.possible_keys:
        bits.append(f"possible keys: {node.possible_keys}")
    if node.key:
        bits.append(f"key: {node.key}")
    elif node.possible_keys:
        bits.append("key: none used")
    if node.filtered is not None:
        bits.append(f"filtered: {node.filtered:.1f}%")
    return bits


def _plan_node_card(node, has_actuals: bool, issues_by_table: dict = None) -> QWidget:
    issues_by_table = issues_by_table or {}
    matched = issues_by_table.get(node.table.lower()) if node.table else None
    if matched is not None:
        border = _SEV_COLOR.get(matched.severity, _BORDER)
        title_color = border if matched.severity in ("CRITICAL", "HIGH") else _TEXT
    else:
        border = _BORDER
        title_color = _TEXT

    card = QFrame()
    card.setStyleSheet(
        f"background:{_PANEL_BG}; border:1.5px solid {border}; border-radius:6px;"
    )
    v = QVBoxLayout(card)
    v.setContentsMargins(10, 6, 10, 6)
    v.setSpacing(1)

    title = QLabel(node.node_type[:70] or "?")
    title.setStyleSheet(
        f"color:{title_color}; font-size:12px; font-weight:700; border:none;"
    )
    title.setWordWrap(True)
    title.setMaximumWidth(220)
    v.addWidget(title)

    if node.table:
        t = QLabel(node.table)
        t.setStyleSheet(f"color:{_INFO_COLOR}; font-size:11px; border:none;")
        v.addWidget(t)

    bits = []
    if node.cost:
        bits.append(f"cost {node.cost:.2f}")
    if node.rows_estimated or node.rows_actual:
        bits.append(
            f"rows {node.rows_estimated:,} → {node.rows_actual:,}" if has_actuals
            else f"~{node.rows_estimated:,} rows"
        )
    if has_actuals:
        bits.append(f"{node.time_ms:.2f} ms")
    if bits:
        stat_lbl = QLabel("  ·  ".join(bits))
        stat_lbl.setStyleSheet(f"color:{_MUTED}; font-size:10px; border:none;")
        stat_lbl.setWordWrap(True)
        v.addWidget(stat_lbl)

    explain_bits = _plan_explain_bits(node)
    if explain_bits:
        explain_lbl = QLabel("  ·  ".join(explain_bits))
        explain_lbl.setStyleSheet(f"color:{_MUTED}; font-size:10px; border:none;")
        explain_lbl.setWordWrap(True)
        v.addWidget(explain_lbl)

    return card


def _plan_tree_widget(node, has_actuals: bool, issues_by_table: dict = None) -> QWidget:
    """Recreates the plan hierarchy as a small vertical flow of node cards
    connected by simple line glyphs — built entirely from the parsed
    ProfileNode tree (never invented)."""
    issues_by_table = issues_by_table or {}
    container = QWidget()
    cv = QVBoxLayout(container)
    cv.setContentsMargins(0, 0, 0, 0)
    cv.setSpacing(2)

    row = QHBoxLayout()
    row.addStretch()
    row.addWidget(_plan_node_card(node, has_actuals, issues_by_table))
    row.addStretch()
    cv.addLayout(row)

    if node.children:
        arrow = QLabel("│")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setStyleSheet(f"color:{_BORDER}; font-size:14px;")
        cv.addWidget(arrow)

        children_row = QHBoxLayout()
        children_row.setSpacing(16)
        children_row.addStretch()
        for child in node.children:
            children_row.addWidget(_plan_tree_widget(child, has_actuals, issues_by_table))
        children_row.addStretch()
        cv.addLayout(children_row)

    return container


def _compact_plan_summary(node, has_actuals: bool, issues_by_table: dict) -> QWidget:
    """A one-row detail strip for a single-operator plan — the common case
    for a simple lookup or small-table scan — instead of the bordered
    flow-chart card, which is unnecessary ceremony for one node. Uses the
    same _stat_card tiles as Performance Summary for visual consistency."""
    matched = issues_by_table.get(node.table.lower()) if node.table else None
    sev_color = _SEV_COLOR.get(matched.severity) if matched else None

    cards = [
        _stat_card(
            "Access Type",
            _access_type_label(node.access_type) if node.access_type else (node.node_type[:32] or "—"),
            color=sev_color,
        ),
    ]

    if has_actuals and node.rows_estimated:
        rows_val = f"{node.rows_estimated:,} → {node.rows_actual:,}"
    elif has_actuals:
        rows_val = f"{node.rows_actual:,}"
    elif node.rows_estimated:
        rows_val = f"{node.rows_estimated:,}"
    else:
        rows_val = "—"
    cards.append(_stat_card("Rows", rows_val))

    if node.possible_keys:
        cards.append(_stat_card("Possible Keys", node.possible_keys))
    cards.append(_stat_card("Key", node.key or "None"))
    if node.filtered is not None:
        cards.append(_stat_card("Filtered", f"{node.filtered:.1f}%"))
    if has_actuals:
        cards.append(_stat_card("Time", _format_duration(node.time_ms / 1000.0)))
    elif node.cost:
        cards.append(_stat_card("Cost", f"{node.cost:.2f}"))

    container = QWidget()
    v = QVBoxLayout(container)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    if node.table:
        t = QLabel(node.table)
        t.setStyleSheet(f"color:{_INFO_COLOR}; font-size:12px; font-weight:600;")
        v.addWidget(t)
    v.addWidget(_summary_row(cards))
    return container


def _count_plan_nodes(node) -> int:
    return 1 + sum(_count_plan_nodes(c) for c in node.children)


def _plan_depth(node) -> int:
    return 1 + (max((_plan_depth(c) for c in node.children), default=0) if node.children else 0)


def _plan_pane_widget(estimate, label: str) -> QWidget:
    """One side of the Plan Comparison split view — a query label plus the
    same tree/compact-summary rendering _CostProfileTab._add_plan_section
    uses for a single query's plan, just returning a widget instead of
    appending to a results layout so two of these can sit side by side."""
    container = QWidget()
    v = QVBoxLayout(container)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)

    hdr = QLabel(label)
    hdr.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600;")
    v.addWidget(hdr)

    issues_by_table = _issues_by_table(estimate.issues)
    root = estimate.plan_tree
    total = _count_plan_nodes(root)
    if total > 40:
        info = QLabel(f"Plan has {total} operators — too large to render here.")
        info.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        info.setWordWrap(True)
        v.addWidget(info)
    elif total == 1:
        v.addWidget(_compact_plan_summary(root, False, issues_by_table))
    else:
        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setFixedHeight(min(95 * _plan_depth(root) + 40, 280))
        holder.setWidget(_plan_tree_widget(root, False, issues_by_table))
        v.addWidget(holder)

    return container


def _issue_card(issue) -> QWidget:
    """One Issues-section entry: severity + title, the full explanation
    (which already names the affected table/operator — see query_cost.py's
    Issue.message convention), and the recommendation, if any."""
    color = _SEV_COLOR.get(issue.severity, _MUTED)
    title = query_cost._CODE_LABELS.get(issue.code, issue.code.replace("_", " ").title())

    card = QFrame()
    card.setStyleSheet(
        f"QFrame {{ background:{_PANEL_BG}; border:1px solid {_BORDER};"
        f" border-left:3px solid {color}; border-radius:6px; }}"
    )
    v = QVBoxLayout(card)
    v.setContentsMargins(10, 8, 10, 8)
    v.setSpacing(4)

    head = QHBoxLayout()
    head.setSpacing(6)
    head.addWidget(_text_chip(issue.severity, color, width=72))
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:700; border:none;")
    head.addWidget(title_lbl, 1)
    v.addLayout(head)

    msg = QLabel(issue.message)
    msg.setWordWrap(True)
    msg.setStyleSheet(f"color:{_TEXT}; font-size:12px; border:none;")
    msg.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    v.addWidget(msg)

    if issue.suggestion:
        fix = QLabel(f"Recommendation: {issue.suggestion}")
        fix.setWordWrap(True)
        fix.setStyleSheet(f"color:{_INFO_COLOR}; font-size:11px; border:none;")
        fix.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        v.addWidget(fix)

    return card


_SHARED_STYLESHEET = f"""
    QWidget {{
        background: {_BG};
        color: {_TEXT};
        font-size: 13px;
    }}
    QSplitter::handle {{ background: {_BORDER}; }}
    QScrollArea {{ background: {_BG}; border: none; }}
    QTableWidget, QTreeWidget {{
        background: {_PANEL_BG};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        gridline-color: {_BORDER};
        font-size: 12px;
    }}
    QHeaderView::section {{
        background: #3a3a3c;
        color: {_MUTED};
        font-size: 11px;
        font-weight: 600;
        text-transform: uppercase;
        padding: 4px 8px;
        border: none;
        border-right: 1px solid {_BORDER};
    }}
    QTableWidget::item, QTreeWidget::item {{ padding: 4px 8px; }}
    QPushButton#primaryBtn {{
        background: #0A84FF;
        color: #fff;
        border: none;
        border-radius: 6px;
        font-weight: 600;
        font-size: 13px;
        padding: 0 20px;
    }}
    QPushButton#primaryBtn:hover  {{ background: #228BFF; }}
    QPushButton#primaryBtn:pressed {{ background: #0066CC; }}
    QPushButton#primaryBtn:disabled {{
        background: #2c2c2e;
        color: {_MUTED};
        border: 1px solid {_BORDER};
    }}
"""


# ===========================================================================
# Tab 1 — Cost & Profile
# ===========================================================================

class _CostWorker(QObject):
    done    = Signal(object)   # CostEstimate
    errored = Signal(str)

    def __init__(self, db_service, sql: str):
        super().__init__()
        self._db  = db_service
        self._sql = sql

    def run(self):
        from services.db_service import DbService
        dedicated = DbService()
        try:
            dedicated.connect(self._db._config)
            self.done.emit(query_cost.estimate_cost(dedicated, self._sql))
        except Exception as ex:
            self.errored.emit(str(ex))
        finally:
            try:
                dedicated.disconnect()
            except Exception:
                pass


class _ProfileWorker(QObject):
    done    = Signal(object)   # QueryProfile
    errored = Signal(str)

    def __init__(self, db_service, sql: str):
        super().__init__()
        self._db  = db_service
        self._sql = sql

    def run(self):
        from services.db_service import DbService
        dedicated = DbService()
        try:
            dedicated.connect(self._db._config)
            self.done.emit(query_cost.build_profile(dedicated, self._sql))
        except Exception as ex:
            self.errored.emit(str(ex))
        finally:
            try:
                dedicated.disconnect()
            except Exception:
                pass


class _CostProfileTab(QWidget):
    """Single-query cost estimate (plan-only, never executes) plus an
    opt-in post-run profile (EXPLAIN ANALYZE — executes the query).

    The Estimate and Profile sections each live in their own container
    within the results pane and are cleared/rebuilt independently (see
    _render_estimate/_render_profile) — so running one never erases the
    other; both can be on screen together (section 12's "do not show
    stale analysis results" still holds per-section: each section only
    ever shows its own latest run or historical snapshot, never a mix)."""

    def __init__(self, db_service, initial_query: str = "", initial_cost_detail: dict = None,
                 initial_profile_detail: dict = None, query_history=None,
                 history_entry_id: str = None, connection_name: str = "", parent=None):
        super().__init__(parent)
        self._db = db_service
        self._dialect = getattr(db_service, "db_type", "") or ""
        self._cost_thread = None
        self._cost_worker = None
        self._profile_thread = None
        self._profile_worker = None
        # When history_entry_id is given (the dialog was opened from an
        # existing history row — e.g. the History panel's "View Cost &
        # Profile" action), a live Estimate/Profile run here is persisted
        # back onto that entry. Otherwise (the "current tab's query" entry
        # point — Database menu / status badge — which has no existing
        # entry to write back to) the first successful run instead creates
        # a new one (issue #339), and every later run in this dialog
        # session updates that same entry — see _persist_to_history.
        self._query_history = query_history
        self._history_entry_id = history_entry_id
        self._connection_name = connection_name
        self._build_ui(initial_query, initial_cost_detail, initial_profile_detail)

    def _build_ui(self, initial_query: str, initial_cost_detail: dict = None,
                  initial_profile_detail: dict = None):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        # ── Top pane: compact, resizable SQL editor + actions ──────────
        editor_pane = QWidget()
        ep = QVBoxLayout(editor_pane)
        ep.setContentsMargins(0, 0, 0, 6)
        ep.setSpacing(6)

        self._editor = CodeEditor()
        self._editor.setPlaceholderText("Paste or write a query to analyze…")
        self._editor.setPlainText(initial_query)
        self._editor.setMinimumHeight(48)
        SqlHighlighter(self._editor.document())
        ep.addWidget(self._editor, 1)

        note = QLabel(
            "Estimate Cost runs EXPLAIN only — it never executes the query. "
            "Run Profile executes the query for real (EXPLAIN ANALYZE)."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        ep.addWidget(note)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        btn_row.addWidget(self._status_lbl, 1)

        self._estimate_btn = QPushButton("▶  Estimate Cost")
        self._estimate_btn.setObjectName("primaryBtn")
        self._estimate_btn.setFixedHeight(32)
        self._estimate_btn.clicked.connect(self._start_estimate)
        btn_row.addWidget(self._estimate_btn)

        self._profile_btn = QPushButton("▶  Run Profile (executes query)")
        self._profile_btn.setFixedHeight(32)
        self._profile_btn.setStyleSheet(
            f"QPushButton {{ background:transparent; color:{_TEXT};"
            f" border:1px solid {_BORDER}; border-radius:6px; padding:0 16px; font-size:13px; }}"
            f" QPushButton:hover {{ border-color:{_WARN_COLOR}; color:{_WARN_COLOR}; }}"
            f" QPushButton:disabled {{ color:{_MUTED}; border-color:{_BORDER}; }}"
        )
        self._profile_btn.clicked.connect(self._start_profile)
        if self._dialect not in ("mysql", "postgresql"):
            self._profile_btn.setEnabled(False)
            self._profile_btn.setToolTip(
                "Profiling isn't available for this dialect — only the "
                "plan-only estimate above."
            )
        btn_row.addWidget(self._profile_btn)

        ep.addLayout(btn_row)
        splitter.addWidget(editor_pane)

        # ── Bottom pane: results ────────────────────────────────────────
        results_pane = QWidget()
        rp = QVBoxLayout(results_pane)
        rp.setContentsMargins(0, 6, 0, 0)
        rp.setSpacing(6)
        rp.addWidget(_divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._results_widget = QWidget()
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 4, 0, 4)
        self._results_layout.setSpacing(6)

        # Estimate and Profile each get a persistent container, added once
        # here and never removed — _render_estimate/_render_profile only
        # ever clear+repopulate their own container's inner layout, so one
        # can be showing while the other reruns, and both can be visible
        # together.
        self._empty_lbl = QLabel("Run Estimate Cost or Run Profile to analyze this query.")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(f"color:{_MUTED}; font-size:13px; padding:24px;")
        self._results_layout.addWidget(self._empty_lbl)

        self._estimate_container = QWidget()
        self._estimate_layout = QVBoxLayout(self._estimate_container)
        self._estimate_layout.setContentsMargins(0, 0, 0, 0)
        self._estimate_layout.setSpacing(6)
        self._results_layout.addWidget(self._estimate_container)

        self._section_divider = _divider()
        self._results_layout.addWidget(self._section_divider)

        self._profile_container = QWidget()
        self._profile_layout = QVBoxLayout(self._profile_container)
        self._profile_layout.setContentsMargins(0, 0, 0, 0)
        self._profile_layout.setSpacing(6)
        self._results_layout.addWidget(self._profile_container)

        self._results_layout.addStretch()

        scroll.setWidget(self._results_widget)
        rp.addWidget(scroll, 1)
        splitter.addWidget(results_pane)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([130, 560])
        root.addWidget(splitter, 1)

        historical_estimate = query_cost.estimate_from_dict(initial_cost_detail)
        historical_profile = query_cost.profile_from_dict(initial_profile_detail)
        if historical_estimate:
            self._render_estimate(historical_estimate, from_history=True)
        if historical_profile:
            self._render_profile(historical_profile, from_history=True)
        if historical_estimate or historical_profile:
            shown = " and ".join(
                n for n, present in (("Estimate", historical_estimate), ("Profile", historical_profile)) if present
            )
            self._status_lbl.setText(
                f"Showing the {shown} captured when this query last ran — "
                "Estimate Cost / Run Profile re-check against the database now."
            )
        else:
            self._show_empty_state()

    def set_query(self, sql: str):
        self._editor.setPlainText(sql)

    # ─── Result-pane state helpers ──────────────────────────────────────

    def _update_empty_state(self):
        has_estimate = self._estimate_layout.count() > 0
        has_profile = self._profile_layout.count() > 0
        self._empty_lbl.setVisible(not has_estimate and not has_profile)
        self._section_divider.setVisible(has_estimate and has_profile)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                # hide() first — deleteLater() alone leaves the widget visible
                # (still a child of the results pane, at its old geometry)
                # until the deferred delete actually runs, so a stale result
                # from the previous run would otherwise show through the new
                # one for a frame or two.
                w.hide()
                w.deleteLater()

    def _show_empty_state(self):
        self._clear_layout(self._estimate_layout)
        self._clear_layout(self._profile_layout)
        self._update_empty_state()

    def _show_loading(self, message: str, target: str):
        layout = self._estimate_layout if target == "estimate" else self._profile_layout
        self._clear_layout(layout)
        lbl = QLabel(message)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"color:{_MUTED}; font-size:13px; padding:24px;")
        layout.addWidget(lbl)
        self._update_empty_state()

    def _render_failure(self, title: str, message: str, target: str):
        layout = self._estimate_layout if target == "estimate" else self._profile_layout
        self._clear_layout(layout)
        layout.addWidget(_section_label(title))
        layout.addWidget(_divider())
        layout.addWidget(_check_row("fail", title, message))
        self._update_empty_state()

    def _set_busy(self, busy: bool):
        self._estimate_btn.setEnabled(not busy)
        self._profile_btn.setEnabled(not busy and self._dialect in ("mysql", "postgresql"))

    def _persist_to_history(self, **fields):
        """Write *fields* onto the history entry this dialog was opened
        for — see _CostProfileTab.__init__. When there isn't one yet (the
        "current tab's query" entry point), create one instead so a
        standalone Analyze Query run doesn't vanish once the dialog closes
        (issue #339); the id is remembered so a later run in this same
        dialog session (e.g. Profile after Estimate) updates that entry
        rather than creating a second one."""
        if not self._query_history:
            return
        if self._history_entry_id:
            self._query_history.update_entry(self._history_entry_id, **fields)
        else:
            sql = self._editor.toPlainText().strip()
            self._history_entry_id = self._query_history.add_query(
                sql, self._connection_name, **fields,
            )

    # ─── Cost estimate (plan-only) ───────────────────────────────────────

    def _start_estimate(self):
        sql = self._editor.toPlainText().strip()
        if not sql:
            return
        self._set_busy(True)
        self._estimate_btn.setText("Estimating…")
        self._status_lbl.setText("Running EXPLAIN…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._show_loading("Analyzing query…", target="estimate")

        worker = _CostWorker(self._db, sql)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_estimate_done)
        worker.errored.connect(self._on_estimate_error)
        worker.done.connect(thread.quit)
        worker.errored.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._cost_thread = thread
        self._cost_worker = worker
        thread.start()

    def _on_estimate_done(self, result: query_cost.CostEstimate):
        self._set_busy(True)
        self._estimate_btn.setEnabled(True)
        self._estimate_btn.setText("▶  Estimate Cost")
        self._profile_btn.setEnabled(self._dialect in ("mysql", "postgresql"))
        if result.error:
            self._status_lbl.setText(f"Failed: {result.error}")
            self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")
            self._render_failure("Query Analysis Failed", result.error, target="estimate")
            return
        self._status_lbl.setText("")
        self._render_estimate(result)
        self._persist_to_history(
            cost_score=result.score, cost_label=result.label,
            cost_detail=query_cost.estimate_to_dict(result),
        )

    def _on_estimate_error(self, message: str):
        self._set_busy(False)
        self._estimate_btn.setText("▶  Estimate Cost")
        self._status_lbl.setText(f"Error: {message}")
        self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")
        self._render_failure("Query Analysis Failed", message, target="estimate")

    def _render_estimate(self, result: query_cost.CostEstimate, from_history: bool = False):
        self._clear_layout(self._estimate_layout)
        add = self._estimate_layout.addWidget

        risk = query_cost.risk_level_for(result.issues)
        cost_label = _DIALECT_COST_LABEL.get(result.dialect, "Estimated Cost")
        cost_tooltip = _DIALECT_COST_TOOLTIP.get(result.dialect, "")

        add(_section_label("Performance Summary"))
        if from_history:
            add(_mode_badge("FROM HISTORY — CAPTURED WHEN THIS QUERY LAST RAN", _MUTED))
        else:
            add(_mode_badge("ESTIMATE — QUERY NOT EXECUTED", _INFO_COLOR))
        add(_summary_row([
            _stat_card("QForge Risk Score", str(result.score),
                       color=_RISK_COLOR.get(risk), sub=risk,
                       tooltip="A QForge-derived severity score based on the issues "
                               "detected below — not a MySQL metric."),
            _stat_card(cost_label, _na(result.native_cost, "{:,.2f}"),
                       sub="Not milliseconds", tooltip=cost_tooltip),
            _stat_card("Estimated Rows Examined", _na(result.estimated_rows, "{:,}"),
                       tooltip=_ROWS_EXAMINED_TOOLTIP),
            _stat_card("Execution", "Not executed"),
        ]))

        self._add_issues_section(add, result.issues)

        if result.plan_tree:
            self._add_plan_section(add, result.plan_tree, has_actuals=False, issues=result.issues)

        self._add_recommendations_section(add, result.issues)
        self._update_empty_state()

    # ─── Profile (post-run, executes the query) ──────────────────────────

    def _confirm_mutating_profile(self, sql: str) -> bool:
        """Section-11 safety gate: EXPLAIN ANALYZE genuinely runs the
        statement, so a write must get an explicit confirmation, not run
        silently just because the user clicked Profile. DbService's own
        read-only guard (services/db_service.py) remains the real backstop
        underneath this — this is only the UX warning in front of it."""
        try:
            from services.query_classifier import classify
            if not classify(sql).is_write:
                return True
        except Exception:
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Run Profile Executes This Query")
        box.setText("This operation executes the query.")
        box.setInformativeText(
            "This statement may modify database data. EXPLAIN ANALYZE runs "
            "it for real, exactly as written — not a simulation."
        )
        cancel_btn = box.addButton("Cancel", QMessageBox.RejectRole)
        exec_btn = box.addButton("Execute && Profile", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel_btn)
        box.setStyleSheet(
            f"QMessageBox {{ background:{_BG}; }}"
            f" QMessageBox QLabel {{ color:{_TEXT}; background:transparent; }}"
            f" QPushButton {{ background:{_PANEL_BG}; color:{_TEXT};"
            f" border:1px solid {_BORDER}; border-radius:4px; padding:5px 16px; }}"
            f" QPushButton:hover {{ border-color:{_WARN_COLOR}; }}"
        )
        box.exec()
        return box.clickedButton() is exec_btn

    def _start_profile(self):
        sql = self._editor.toPlainText().strip()
        if not sql:
            return
        if not self._confirm_mutating_profile(sql):
            return

        self._set_busy(True)
        self._profile_btn.setText("Running…")
        self._status_lbl.setText("Executing query with EXPLAIN ANALYZE…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._show_loading("Executing query and analyzing the plan…", target="profile")

        worker = _ProfileWorker(self._db, sql)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_profile_done)
        worker.errored.connect(self._on_profile_error)
        worker.done.connect(thread.quit)
        worker.errored.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._profile_thread = thread
        self._profile_worker = worker
        thread.start()

    def _on_profile_done(self, result: query_cost.QueryProfile):
        self._set_busy(False)
        self._profile_btn.setText("▶  Run Profile (executes query)")
        if not result.supported:
            self._status_lbl.setText("")
            self._render_failure("Profile Not Available", result.error or "Not supported for this dialect.",
                                  target="profile")
            return
        if result.error:
            self._status_lbl.setText(f"Failed: {result.error}")
            self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")
            self._render_failure("Query Analysis Failed", result.error, target="profile")
            return
        self._status_lbl.setText("")
        self._render_profile(result)
        self._persist_to_history(
            execution_time=result.total_time_ms / 1000.0,
            profile_detail=query_cost.profile_to_dict(result),
        )

    def _on_profile_error(self, message: str):
        self._set_busy(False)
        self._profile_btn.setText("▶  Run Profile (executes query)")
        self._status_lbl.setText(f"Error: {message}")
        self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")
        self._render_failure("Query Analysis Failed", message, target="profile")

    def _render_profile(self, result: query_cost.QueryProfile, from_history: bool = False):
        self._clear_layout(self._profile_layout)
        add = self._profile_layout.addWidget

        risk = query_cost.risk_level_for(result.issues)
        cost_label = _DIALECT_COST_LABEL.get(result.dialect, "Estimated Cost")
        cost_tooltip = _DIALECT_COST_TOOLTIP.get(result.dialect, "")
        native_cost = result.root.cost if result.root and result.root.cost else None
        est_rows = result.root.rows_estimated if result.root else None
        actual_rows = result.root.rows_actual if result.root else None

        add(_section_label("Performance Summary"))
        if from_history:
            add(_mode_badge("FROM HISTORY — CAPTURED WHEN THIS QUERY WAS LAST PROFILED", _MUTED))
        else:
            add(_mode_badge("PROFILE — QUERY EXECUTED", _PASS_COLOR))
        add(_summary_row([
            _stat_card("QForge Risk Score", str(query_cost.score_issues(result.issues)),
                       color=_RISK_COLOR.get(risk), sub=risk,
                       tooltip="A QForge-derived severity score based on the issues "
                               "detected below — not a MySQL metric."),
            _stat_card(cost_label, _na(native_cost, "{:,.2f}"),
                       sub="Not milliseconds", tooltip=cost_tooltip),
            _stat_card("Estimated Rows Examined", _na(est_rows, "{:,}"),
                       tooltip=_ROWS_EXAMINED_TOOLTIP),
            _stat_card("Actual Execution", _format_duration(result.total_time_ms / 1000.0)),
            _stat_card("Actual Rows", _na(actual_rows, "{:,}")),
        ]))

        self._add_issues_section(add, result.issues)

        if result.root:
            self._add_plan_section(add, result.root, has_actuals=True, issues=result.issues)
            self._add_estimate_vs_actual_section(add, result.root, result.issues)
            self._add_detailed_profile_section(add, result.root, result.issues)

        self._add_recommendations_section(add, result.issues)
        self._update_empty_state()

    # ─── Shared result sections ───────────────────────────────────────────

    def _add_issues_section(self, add, issues: list):
        add(_section_label(f"Issues ({len(issues)})"))
        add(_divider())
        if not issues:
            add(_check_row("pass", "No issues detected."))
            return
        for issue in sorted(issues, key=lambda i: query_cost.SEVERITY_SCORE[i.severity], reverse=True):
            add(_issue_card(issue))

    def _add_plan_section(self, add, root, has_actuals: bool, issues: list = None):
        add(_section_label("Execution Plan"))
        add(_divider())

        issues_by_table = _issues_by_table(issues)
        total = _count_plan_nodes(root)
        if total > 40:
            add(_check_row(
                "info",
                f"Plan has {total} operators — see Detailed Profile below.",
                "The visual diagram is skipped for very large plans to keep this dialog responsive.",
            ))
            return

        if total == 1:
            add(_compact_plan_summary(root, has_actuals, issues_by_table))
            return

        holder = QScrollArea()
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        holder.setFixedHeight(min(95 * _plan_depth(root) + 40, 320))
        holder.setWidget(_plan_tree_widget(root, has_actuals, issues_by_table))
        add(holder)

    def _add_estimate_vs_actual_section(self, add, root, issues: list):
        add(_section_label("Estimate vs Actual"))
        add(_divider())

        tbl = QTableWidget(3, 3)
        tbl.setHorizontalHeaderLabels(["Metric", "Estimated", "Actual"])
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionMode(QAbstractItemView.NoSelection)
        tbl.setFixedHeight(3 * 30 + 40)
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        rows = [
            ("Rows", f"{root.rows_estimated:,}" if root.rows_estimated else "N/A", f"{root.rows_actual:,}"),
            ("Time", "—", _format_duration(root.time_ms / 1000.0)),
            ("Loops", "—", str(root.loops)),
        ]
        for r_i, (metric, est, act) in enumerate(rows):
            for c_i, val in enumerate((metric, est, act)):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignVCenter | (Qt.AlignLeft if c_i == 0 else Qt.AlignCenter))
                tbl.setItem(r_i, c_i, item)
        add(tbl)

        mismatches = [i for i in issues if i.code == "ESTIMATE_MISMATCH"]
        if mismatches:
            add(_section_label("Estimation Mismatch"))
            for issue in mismatches:
                add(_check_row("warn", issue.message, issue.suggestion))

    def _add_detailed_profile_section(self, add, root, issues: list = None):
        add(_section_label("Detailed Profile"))
        add(_divider())
        tree = QTreeWidget()
        tree.setHeaderLabels(["Operator", "Table", "Est. Rows", "Actual Rows", "Time (ms)", "Loops"])
        tree.setColumnCount(6)
        tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        tree.setMinimumHeight(min(28 * _count_plan_nodes(root) + 40, 360))
        self._add_tree_node(tree, root, _issues_by_table(issues))
        tree.expandAll()
        add(tree)

    def _add_tree_node(self, tree_or_item, node, issues_by_table: dict):
        item = QTreeWidgetItem([
            node.node_type, node.table,
            f"{node.rows_estimated:,}", f"{node.rows_actual:,}",
            f"{node.time_ms:.2f}", str(node.loops),
        ])
        # Same verdict as the Issues section and the Execution Plan diagram
        # (see _issues_by_table) — a scan/spill only reads as a problem
        # here when the rule engine actually flagged it as one.
        matched = issues_by_table.get(node.table.lower()) if node.table else None
        is_spill = "external" in (node.extra or "").lower()
        if is_spill or (matched is not None and matched.severity in ("CRITICAL", "HIGH")):
            for col in range(6):
                item.setForeground(col, QBrush(QColor(_FAIL_COLOR)))
        elif matched is not None and matched.severity == "MEDIUM":
            for col in range(6):
                item.setForeground(col, QBrush(QColor(_WARN_COLOR)))
        if isinstance(tree_or_item, QTreeWidget):
            tree_or_item.addTopLevelItem(item)
        else:
            tree_or_item.addChild(item)
        for child in node.children:
            self._add_tree_node(item, child, issues_by_table)
        return item

    def _add_recommendations_section(self, add, issues: list):
        add(_section_label("Recommendations"))
        add(_divider())

        recs = []
        seen = set()
        for issue in sorted(issues, key=lambda i: query_cost.SEVERITY_SCORE[i.severity], reverse=True):
            if issue.suggestion and issue.suggestion not in seen:
                seen.add(issue.suggestion)
                recs.append(issue.suggestion)

        if not recs:
            add(_check_row("pass", "No recommendations — this query looks efficient."))
            return

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 0, 0, 4)
        bv.setSpacing(4)
        for i, rec in enumerate(recs, 1):
            lbl = QLabel(f"{i}.  {rec}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            bv.addWidget(lbl)
        add(body)

        copy_row_w = QWidget()
        copy_row = QHBoxLayout(copy_row_w)
        copy_row.setContentsMargins(0, 0, 0, 0)
        copy_row.addStretch()
        copy_btn = QPushButton("⎘ Copy Recommendations")
        copy_btn.setFixedHeight(24)
        copy_btn.setStyleSheet(
            f"background:transparent; color:{_MUTED}; border:1px solid {_BORDER};"
            f" border-radius:4px; font-size:11px; padding:0 10px;"
        )
        copy_btn.clicked.connect(
            lambda: _copy_with_feedback(
                copy_btn,
                "\n".join(f"{i}. {r}" for i, r in enumerate(recs, 1)),
                "⎘ Copy Recommendations",
            )
        )
        copy_row.addWidget(copy_btn)
        add(copy_row_w)


# ===========================================================================
# Tab 2 — Compare Queries (services/query_verifier.py's existing UI, moved
# from being the whole QueryVerifierDialog to one tab of this dialog)
# ===========================================================================

_PARAM_RE = re.compile(
    r"\$\{(\w+)\}"          # ${varName}
    r"|(?<![:\w]):(\w+)"     # :varName  (not ::cast)
    r"|\{\{(\w+)\}\}"        # {{varName}}
)


def _extract_params(sql: str) -> list[str]:
    seen: list[str] = []
    for m in _PARAM_RE.finditer(sql):
        name = m.group(1) or m.group(2) or m.group(3)
        if name and name not in seen:
            seen.append(name)
    return seen


def _substitute_params(sql: str, values: dict) -> str:
    def replacer(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return values.get(name, m.group(0))
    return _PARAM_RE.sub(replacer, sql)


class _VerifyWorker(QObject):
    done    = Signal(object)   # VerifyResult
    errored = Signal(str)

    def __init__(self, db_service, original_query: str, optimised_query: str,
                 row_limit: int = 0):
        super().__init__()
        self._db        = db_service
        self._orig      = original_query
        self._opt       = optimised_query
        self._row_limit = row_limit

    def run(self):
        from services.db_service import DbService
        dedicated = DbService()
        try:
            dedicated.connect(self._db._config)
            verifier = QueryVerifier(dedicated)
            result   = verifier.verify(self._orig, self._opt, row_limit=self._row_limit)
            self.done.emit(result)
        except Exception as ex:
            self.errored.emit(str(ex))
        finally:
            try:
                dedicated.disconnect()
            except Exception:
                pass


class _CompareQueriesTab(QWidget):
    """Query 1-vs-Query 2 diff — unchanged logic from the former
    QueryVerifierDialog, just embedded as a tab instead of a standalone
    dialog. QForge never assumes Query 2 is the "optimized" one — which is
    faster, has the lower cost, or produces equivalent results is decided
    entirely by what verification/analysis below actually finds."""

    def __init__(self, db_service, initial_query: str = "", parent=None):
        super().__init__(parent)
        self._db          = db_service
        self._thread      = None
        self._worker      = None
        self._cancel_flag = threading.Event()
        self._last_row_limit = 0

        self._build_ui(initial_query)
        self._apply_styles()

    def _build_ui(self, initial_query: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(0)

        outer_splitter = QSplitter(Qt.Vertical)
        outer_splitter.setHandleWidth(6)
        outer_splitter.setChildrenCollapsible(False)

        top_pane = QWidget()
        top_v = QVBoxLayout(top_pane)
        top_v.setContentsMargins(0, 0, 0, 6)
        top_v.setSpacing(6)

        editor_splitter = QSplitter(Qt.Horizontal)
        editor_splitter.setHandleWidth(6)

        for side, label_text, query_text in [
            ("orig",  "Query 1",   ""),
            ("opt",   "Query 2",  initial_query),
        ]:
            box = QWidget()
            bv  = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(4)

            hdr_row = QHBoxLayout()
            hdr_row.setContentsMargins(0, 0, 0, 0)

            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600; padding:2px 0;")
            hdr_row.addWidget(lbl, 1)
            bv.addLayout(hdr_row)

            find_bar = QWidget()
            find_bar.setVisible(False)
            fb_row = QHBoxLayout(find_bar)
            fb_row.setContentsMargins(0, 2, 0, 2)
            fb_row.setSpacing(4)

            find_input = QLineEdit()
            find_input.setPlaceholderText("Search…")
            find_input.setFixedHeight(26)
            find_input.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" border-radius:4px; padding:0 6px; font-size:12px;"
            )
            fb_row.addWidget(find_input, 1)

            match_lbl = QLabel("")
            match_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
            match_lbl.setFixedWidth(60)
            fb_row.addWidget(match_lbl)

            prev_btn = QPushButton("▲")
            next_btn = QPushButton("▼")
            close_btn = QPushButton("✕")
            for b in (prev_btn, next_btn, close_btn):
                b.setFixedSize(22, 22)
                b.setStyleSheet(f"background:transparent; color:{_MUTED}; border:none; font-size:11px;")
            fb_row.addWidget(prev_btn)
            fb_row.addWidget(next_btn)
            fb_row.addWidget(close_btn)
            bv.addWidget(find_bar)

            editor = CodeEditor()
            editor.setPlaceholderText(f"Paste {label_text.lower()} here…")
            editor.setPlainText(query_text)
            editor.setMinimumHeight(48)
            SqlHighlighter(editor.document())
            bv.addWidget(editor, 1)

            if side == "orig":
                self._orig_editor = editor
                self._orig_find_bar = find_bar
                self._orig_find_input = find_input
                self._orig_match_lbl = match_lbl
            else:
                self._opt_editor = editor
                self._opt_find_bar = find_bar
                self._opt_find_input = find_input
                self._opt_match_lbl = match_lbl

            _editor, _bar, _inp, _mlbl = editor, find_bar, find_input, match_lbl

            def _show_find(ed=_editor, bar=_bar, inp=_inp):
                bar.setVisible(True)
                inp.setFocus()
                inp.selectAll()

            def _hide_find(ed=_editor, bar=_bar, inp=_inp):
                bar.setVisible(False)
                ed.setFocus()
                cur = ed.textCursor()
                cur.select(QTextCursor.Document)
                fmt = QTextCharFormat()
                cur.setCharFormat(fmt)
                cur.clearSelection()
                ed.setTextCursor(cur)

            def _do_find(text, forward=True, ed=_editor, mlbl=_mlbl):
                if not text:
                    mlbl.setText("")
                    return
                doc = ed.document()
                flags = QTextCursor.FindCaseSensitively if any(c.isupper() for c in text) else QTextCursor.FindFlags()
                if not forward:
                    flags |= QTextCursor.FindBackward
                fmt_hi = QTextCharFormat()
                fmt_hi.setBackground(QColor("#ff9f0a"))
                fmt_hi.setForeground(QColor("#000"))
                fmt_clr = QTextCharFormat()
                cur_all = QTextCursor(doc)
                cur_all.select(QTextCursor.Document)
                cur_all.setCharFormat(fmt_clr)
                count = 0
                c = QTextCursor(doc)
                while True:
                    c = doc.find(text, c, flags & ~QTextCursor.FindBackward)
                    if c.isNull():
                        break
                    c.mergeCharFormat(fmt_hi)
                    count += 1
                mlbl.setText(f"{count} found" if count else "not found")
                mlbl.setStyleSheet(f"color:{'#ff453a' if count == 0 else _MUTED}; font-size:11px;")
                found = ed.find(text, flags)
                if not found:
                    c2 = ed.textCursor()
                    c2.movePosition(QTextCursor.Start if forward else QTextCursor.End)
                    ed.setTextCursor(c2)
                    ed.find(text, flags)

            find_input.textChanged.connect(lambda t, fn=_do_find: fn(t))
            next_btn.clicked.connect(lambda _, inp=_inp, fn=_do_find: fn(inp.text(), True))
            prev_btn.clicked.connect(lambda _, inp=_inp, fn=_do_find: fn(inp.text(), False))
            find_input.returnPressed.connect(lambda inp=_inp, fn=_do_find: fn(inp.text(), True))
            close_btn.clicked.connect(lambda _, hf=_hide_find: hf())

            sc = QShortcut(QKeySequence("Ctrl+F"), box)
            sc.activated.connect(lambda sf=_show_find: sf())
            sc_esc = QShortcut(QKeySequence("Escape"), box)
            sc_esc.activated.connect(lambda hf=_hide_find, bar=_bar: hf() if bar.isVisible() else None)

            editor_splitter.addWidget(box)

        editor_splitter.setSizes([500, 500])
        top_v.addWidget(editor_splitter, 1)

        self._param_box = QGroupBox("Query Parameters")
        self._param_box.setVisible(False)
        self._param_box.setStyleSheet(
            f"QGroupBox {{ color:{_MUTED}; font-size:11px; font-weight:600;"
            f" border:1px solid {_BORDER}; border-radius:6px; margin-top:6px;"
            f" padding:6px 8px; }}"
            f" QGroupBox::title {{ subcontrol-origin:margin; left:8px; padding:0 4px; }}"
        )
        self._param_form = QFormLayout(self._param_box)
        self._param_form.setContentsMargins(8, 12, 8, 6)
        self._param_form.setSpacing(6)
        self._param_inputs: dict = {}
        top_v.addWidget(self._param_box)
        outer_splitter.addWidget(top_pane)

        bottom_pane = QWidget()
        bottom_v = QVBoxLayout(bottom_pane)
        bottom_v.setContentsMargins(0, 6, 0, 0)
        bottom_v.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._status_lbl.setCursor(Qt.IBeamCursor)
        btn_row.addWidget(self._status_lbl, 1)

        self._limit_chk = QCheckBox("Compare first")
        self._limit_chk.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._limit_chk.setToolTip(
            "Wraps each query as SELECT * FROM (...) LIMIT N before running. "
            "Row counts, column comparison, and row-level diff below all "
            "reflect only this sample — not the full result sets."
        )
        btn_row.addWidget(self._limit_chk)

        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(100, 1_000_000)
        self._limit_spin.setValue(1000)
        self._limit_spin.setSingleStep(1000)
        self._limit_spin.setFixedHeight(28)
        self._limit_spin.setFixedWidth(90)
        self._limit_spin.setStyleSheet(
            f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
            f" border-radius:4px; padding:0 4px; font-size:12px;"
        )
        self._limit_spin.setEnabled(False)
        self._limit_chk.toggled.connect(self._limit_spin.setEnabled)
        btn_row.addWidget(self._limit_spin)

        limit_suffix = QLabel("rows")
        limit_suffix.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        btn_row.addWidget(limit_suffix)

        self._run_btn = QPushButton("▶  Run Verification")
        self._run_btn.setObjectName("primaryBtn")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setMinimumWidth(180)
        self._run_btn.clicked.connect(self._start_verification)
        btn_row.addWidget(self._run_btn)

        bottom_v.addLayout(btn_row)
        bottom_v.addWidget(_divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._results_widget = QWidget()
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 4, 0, 4)
        self._results_layout.setSpacing(4)
        self._results_layout.addStretch()

        scroll.setWidget(self._results_widget)
        bottom_v.addWidget(scroll, 1)
        outer_splitter.addWidget(bottom_pane)

        outer_splitter.setStretchFactor(0, 0)
        outer_splitter.setStretchFactor(1, 1)
        outer_splitter.setSizes([150, 560])
        root.addWidget(outer_splitter, 1)

    def _apply_styles(self):
        self._param_timer = QTimer(self)
        self._param_timer.setSingleShot(True)
        self._param_timer.setInterval(400)
        self._param_timer.timeout.connect(self._refresh_params)
        self._orig_editor.textChanged.connect(self._param_timer.start)
        self._opt_editor.textChanged.connect(self._param_timer.start)
        QTimer.singleShot(0, self._refresh_params)

    def set_optimised_query(self, sql: str):
        self._opt_editor.setPlainText(sql)

    # ─── Parameter helpers ──────────────────────────────────────────────

    def _refresh_params(self):
        combined = (self._orig_editor.toPlainText() + "\n" + self._opt_editor.toPlainText())
        params = _extract_params(combined)

        existing_values = {name: inp.text() for name, inp in self._param_inputs.items()}
        while self._param_form.rowCount():
            self._param_form.removeRow(0)
        self._param_inputs.clear()

        for name in params:
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
            inp = QLineEdit()
            inp.setPlaceholderText(f"value for {name}")
            inp.setFixedHeight(26)
            inp.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" border-radius:4px; padding:0 6px; font-size:12px;"
            )
            if name in existing_values:
                inp.setText(existing_values[name])
            self._param_form.addRow(lbl, inp)
            self._param_inputs[name] = inp

        self._param_box.setVisible(bool(params))

    def _resolve_query(self, sql: str) -> str:
        values = {name: inp.text() for name, inp in self._param_inputs.items()}
        missing = [k for k, v in values.items() if not v.strip()]
        if missing:
            raise ValueError(f"Missing values for: {', '.join(missing)}")
        return _substitute_params(sql, values)

    # ─── Verification logic ──────────────────────────────────────────────

    def _start_verification(self):
        orig_raw = self._orig_editor.toPlainText().strip()
        opt_raw  = self._opt_editor.toPlainText().strip()

        if not orig_raw:
            self._status_lbl.setText("Please enter Query 1.")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return
        if not opt_raw:
            self._status_lbl.setText("Please enter Query 2.")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return

        try:
            orig = self._resolve_query(orig_raw)
            opt  = self._resolve_query(opt_raw)
        except ValueError as ve:
            self._status_lbl.setText(str(ve))
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return

        self._clear_results()
        self._run_btn.setEnabled(False)
        self._run_btn.setText("Running…")
        self._status_lbl.setText("Running both queries in the background…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")

        row_limit = self._limit_spin.value() if self._limit_chk.isChecked() else 0
        self._last_row_limit = row_limit
        worker = _VerifyWorker(self._db, orig, opt, row_limit=row_limit)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_verify_done)
        worker.errored.connect(self._on_verify_error)
        worker.done.connect(thread.quit)
        worker.errored.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_verify_done(self, result: VerifyResult):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Verification")
        self._status_lbl.setText("")
        self._render_results(result)

    def _on_verify_error(self, message: str):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Verification")
        self._status_lbl.setText(f"Error: {message}")
        self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")

    # ─── Results rendering ────────────────────────────────────────────────

    def _clear_results(self):
        layout = self._results_layout
        while layout.count():
            item = layout.takeAt(0)
            w    = item.widget()
            if w:
                # See _CostProfileTab._clear_layout — hide() before
                # deleteLater() so a stale previous result never shows
                # through the new one while the deferred delete is pending.
                w.hide()
                w.deleteLater()

    def _render_results(self, r: VerifyResult):
        self._clear_results()
        add = self._results_layout.addWidget

        if r.error:
            self._status_lbl.setText(f"Failed: {r.error}")
            self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")

            err_card = QWidget()
            err_card.setStyleSheet(
                f"background:{_alpha(_FAIL_COLOR, '18')}; border:1px solid {_alpha(_FAIL_COLOR, '55')}; border-radius:6px;"
            )
            ec_layout = QVBoxLayout(err_card)
            ec_layout.setContentsMargins(10, 8, 10, 8)
            ec_layout.setSpacing(6)

            title_row = QHBoxLayout()
            title_row.setSpacing(8)
            title_row.addWidget(_text_chip("FAIL", _FAIL_COLOR, width=52))
            title_lbl = QLabel("Verification failed with an error")
            title_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px; font-weight:600;")
            title_row.addWidget(title_lbl, 1)
            copy_btn = QPushButton("⎘ Copy")
            copy_btn.setFixedHeight(22)
            copy_btn.setStyleSheet(
                f"background:transparent; color:{_MUTED}; border:1px solid {_BORDER};"
                f" border-radius:3px; font-size:11px; padding:0 8px;"
            )
            copy_btn.clicked.connect(lambda: _copy_with_feedback(copy_btn, r.error, "⎘ Copy"))
            title_row.addWidget(copy_btn)
            ec_layout.addLayout(title_row)

            err_txt = QPlainTextEdit(r.error)
            err_txt.setReadOnly(True)
            err_txt.setStyleSheet(
                f"background:transparent; color:{_TEXT}; border:none;"
                f" font-size:12px; font-family:Menlo,Monaco,'Courier New',monospace;"
            )
            err_txt.setFixedHeight(min(22 * r.error.count('\n') + 60, 200))
            err_txt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            ec_layout.addWidget(err_txt)

            add(err_card)
            self._results_layout.addStretch()
            return

        verdict_text  = "RESULTS EQUIVALENT — Query 1 and Query 2 produce the same data" if r.passed \
            else "RESULTS DIFFER — Query 1 and Query 2 produce different data"
        verdict_color = _PASS_COLOR if r.passed else _FAIL_COLOR
        verdict = QLabel(verdict_text)
        verdict.setAlignment(Qt.AlignCenter)
        verdict.setStyleSheet(
            f"background:{_alpha(verdict_color, '22')}; color:{verdict_color};"
            f" border:1px solid {_alpha(verdict_color, '55')};"
            f" border-radius:8px; font-size:13px; font-weight:700;"
            f" padding:10px 16px; margin-bottom:6px;"
        )
        add(verdict)

        self._add_performance_section(add, r)
        self._add_result_equivalence_section(add, r)

        if r.agg_rows:
            self._add_aggregate_section(add, r)

        if r.explain_original or r.explain_optimised:
            add(_section_label("EXPLAIN Plan"))
            add(_divider())

            explain_note = QLabel(
                "Type legend: "
                "<span style='color:#30d158;'>const/eq_ref/ref</span> = index lookup  "
                "<span style='color:#ff9f0a;'>range</span> = partial scan  "
                "<span style='color:#ff453a;'>ALL</span> = full table scan"
            )
            explain_note.setTextFormat(Qt.RichText)
            explain_note.setStyleSheet(f"color:{_MUTED}; font-size:11px; padding:2px 0 4px 0;")
            add(explain_note)

            explain_splitter = QSplitter(Qt.Horizontal)
            explain_splitter.setHandleWidth(4)
            explain_splitter.addWidget(self._build_explain_table(r.explain_original, "Query 1"))
            explain_splitter.addWidget(self._build_explain_table(r.explain_optimised, "Query 2"))
            add(explain_splitter)

            self._add_plan_comparison_section(add, r)

        self._results_layout.addStretch()

    def _add_performance_section(self, add, r: VerifyResult):
        add(_section_label("Performance"))
        add(_divider())

        q1_ms = r.elapsed_original * 1000
        q2_ms = r.elapsed_optimised * 1000
        diff_ms = q2_ms - q1_ms

        if diff_ms == 0:
            perf_text, perf_color = "No significant difference", _MUTED
        elif q1_ms > 0:
            improvement_pct = ((q1_ms - q2_ms) / q1_ms) * 100
            if improvement_pct > 0:
                perf_text, perf_color = f"{improvement_pct:.1f}% faster", _PASS_COLOR
            else:
                perf_text, perf_color = f"{abs(improvement_pct):.1f}% slower", _FAIL_COLOR
        else:
            perf_text, perf_color = "N/A", _MUTED

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 2, 0, 4)
        bv.setSpacing(2)
        bv.addWidget(_kv_row("Query 1", _format_duration(r.elapsed_original)))
        bv.addWidget(_kv_row("Query 2", _format_duration(r.elapsed_optimised)))
        add(body)

        body2 = QWidget()
        bv2 = QVBoxLayout(body2)
        bv2.setContentsMargins(0, 4, 0, 0)
        bv2.setSpacing(2)
        bv2.addWidget(_kv_row("Difference", _format_signed_duration_ms(diff_ms)))
        bv2.addWidget(_kv_row("Performance", perf_text, color=perf_color))
        add(body2)

    def _add_result_equivalence_section(self, add, r: VerifyResult):
        add(_section_label("Result Equivalence"))
        add(_divider())

        if self._last_row_limit:
            add(_check_row(
                "info",
                f"Comparison limited to the first {self._last_row_limit:,} rows of each query.",
                "Row counts and row-level results below reflect only this sample "
                "— not the full result sets.",
            ))

        if r.count_match:
            count_msg = (
                f"Row count matches: {r.count_original:,} rows" if not self._last_row_limit
                else f"Row count matches within the compared sample: {r.count_original:,} rows"
            )
            add(_check_row("pass", count_msg,
                            f"Query 1: {r.count_original:,}   Query 2: {r.count_optimised:,}"))
        else:
            add(_check_row(
                "fail", "Row count mismatch",
                f"Query 1: {r.count_original:,}   Query 2: {r.count_optimised:,}"
                f"   Difference: {abs(r.count_original - r.count_optimised):,}"
            ))

        if r.cols_match:
            add(_check_row(
                "pass", f"Column set matches: {len(r.cols_original)} columns",
                sub=", ".join(r.cols_original[:10]) + ("…" if len(r.cols_original) > 10 else "")
            ))
        else:
            add(_check_row("fail", "Column sets differ"))
            if r.cols_only_in_original:
                add(_check_row(None, f"Query 1 only: {', '.join(r.cols_only_in_original)}"))
            if r.cols_only_in_optimised:
                add(_check_row(None, f"Query 2 only: {', '.join(r.cols_only_in_optimised)}"))

        diff_cols = getattr(r, "_diff_cols", [])
        if not diff_cols:
            add(_check_row("warn", "No common columns — row-level comparison skipped."))
        elif r.rows_only_in_original == 0 and r.rows_only_in_optimised == 0:
            if r.col_diff_rows:
                add(_check_row(
                    "fail",
                    f"{len(r.col_diff_rows)} row(s) have column-level value differences.",
                    sub="Same rows present in both results, but some cell values differ."
                ))
                add(_section_label("Column-Level Diff (cell values differ)"))
                add(self._build_col_diff_table(r.col_diff_rows, diff_cols))
            else:
                if not r.cols_match:
                    add(_check_row("pass", "No differing rows on common columns.",
                                    sub=f"Compared on: {', '.join(diff_cols)}"))
                else:
                    add(_check_row("pass", "No differing rows found."))
        else:
            if r.rows_only_in_original > 0:
                add(_check_row(
                    "fail",
                    f"{r.rows_only_in_original:,} row(s) present in Query 1 but not in Query 2.",
                ))
                if r.diff_sample_original is not None:
                    add(_section_label("Sample (up to 10) — rows only in Query 1"))
                    add(self._build_df_table(r.diff_sample_original))

            if r.rows_only_in_optimised > 0:
                add(_check_row(
                    "fail",
                    f"{r.rows_only_in_optimised:,} row(s) present in Query 2 but not in Query 1.",
                ))
                if r.diff_sample_optimised is not None:
                    add(_section_label("Sample (up to 10) — rows only in Query 2"))
                    add(self._build_df_table(r.diff_sample_optimised))

    def _add_aggregate_section(self, add, r: VerifyResult):
        add(_section_label("Aggregate Checks"))
        add(_divider())

        note = QLabel("Computed on numeric and date/time columns present in both queries.")
        note.setStyleSheet(f"color:{_MUTED}; font-size:11px; padding:0 0 4px 0;")
        add(note)

        agg_table = QTableWidget(len(r.agg_rows), 8)
        agg_table.setHorizontalHeaderLabels([
            "Column", "SUM (Q1)", "SUM (Q2)",
            "MIN (Q1)", "MIN (Q2)",
            "MAX (Q1)", "MAX (Q2)", "Status",
        ])
        agg_table.verticalHeader().setVisible(False)
        agg_table.horizontalHeader().setStretchLastSection(False)
        agg_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        agg_table.setSelectionBehavior(QTableWidget.SelectRows)
        agg_table.setEditTriggers(QTableWidget.NoEditTriggers)
        agg_table.setFixedHeight(min(36 * len(r.agg_rows) + 40, 300))
        agg_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        for row_idx, ar in enumerate(r.agg_rows):
            status_color = _PASS_COLOR if ar.match else _FAIL_COLOR
            status_text  = "MATCH" if ar.match else "DIFFERENT"
            values = [
                ar.column,
                ar.sum_orig, ar.sum_opt,
                ar.min_orig, ar.min_opt,
                ar.max_orig, ar.max_opt,
                status_text,
            ]
            for col_idx, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if col_idx == 7:
                    item.setForeground(QColor(status_color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                if not ar.match and col_idx in (1, 2):
                    item.setBackground(QColor(_alpha(_FAIL_COLOR, "33")))
                agg_table.setItem(row_idx, col_idx, item)

        add(agg_table)

    # Status → (chip, label) for a PlanDelta row in the Plan Changes list.
    _DELTA_STATUS = {
        "regression": ("fail", "Regression"),
        "improvement": ("pass", "Improvement"),
        "changed":     ("info", "Changed"),
        "added":       ("info", "Added"),
        "removed":     ("info", "Removed"),
    }

    def _add_plan_comparison_section(self, add, r: VerifyResult):
        add(_section_label("Plan Comparison"))
        add(_divider())

        est1, est2 = r.cost_original, r.cost_optimised
        if (est1 and not est1.error and est1.plan_tree
                and est2 and not est2.error and est2.plan_tree):
            self._add_tree_plan_comparison(add, est1, est2)
            return

        self._add_flat_plan_comparison(add, r)

    def _add_tree_plan_comparison(self, add, est1, est2):
        """Real plan trees are available for both sides (query_cost's
        estimate_cost() — MySQL via EXPLAIN FORMAT=TREE, PostgreSQL via
        EXPLAIN (FORMAT JSON)) — render them side by side and list what
        actually changed, table by table, via query_cost.diff_plan_trees()."""
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.addWidget(_plan_pane_widget(est1, "Query 1"))
        splitter.addWidget(_plan_pane_widget(est2, "Query 2"))
        add(splitter)

        add(_section_label("Plan Changes"))
        deltas = query_cost.diff_plan_trees(est1, est2)
        if not deltas:
            add(_check_row(
                "pass",
                "No table-level access-path or severity changes detected "
                "between Query 1 and Query 2.",
            ))
            return

        for d in deltas:
            status, label = self._DELTA_STATUS[d.status]
            add(_check_row(status, f"`{d.table}` — {label}", d.reason))

    def _add_flat_plan_comparison(self, add, r: VerifyResult):
        """Fallback when a real plan tree isn't available for one or both
        sides (e.g. cost estimation errored, or an EXPLAIN FORMAT=TREE-
        incompatible MySQL server) — the original flat single-operator
        diff against the bare EXPLAIN rows QueryVerifier already fetched."""
        e1, e2 = r.explain_original, r.explain_optimised
        if not e1 or not e2:
            add(_check_row(
                "info",
                "EXPLAIN plan not available for one or both queries — plan comparison skipped.",
            ))
            return

        if len(e1) != 1 or len(e2) != 1:
            add(_check_row(
                "info",
                "Both plans involve multiple operators — see the EXPLAIN Plan tables "
                "above for the full comparison.",
            ))
            return

        row1, row2 = e1[0], e2[0]

        def _fmt_rows(v: str) -> str:
            try:
                return f"{int(v):,}"
            except (TypeError, ValueError):
                return v or "—"

        body = QWidget()
        bv = QVBoxLayout(body)
        bv.setContentsMargins(0, 2, 0, 4)
        bv.setSpacing(4)
        bv.addWidget(_kv_row("Access type", row1.access_type or "—", row2.access_type or "—"))
        bv.addWidget(_kv_row("Index", row1.key or "None", row2.key or "None"))
        bv.addWidget(_kv_row("Estimated rows", _fmt_rows(row1.rows), _fmt_rows(row2.rows)))
        add(body)

        add(_section_label("Plan Change"))
        if (row1.access_type or "") == (row2.access_type or ""):
            add(_check_row("info", "No significant execution-plan difference detected."))
        else:
            change = QLabel(f"{_access_type_label(row1.access_type)}  →  {_access_type_label(row2.access_type)}")
            change.setStyleSheet(f"color:{_TEXT}; font-size:13px; font-weight:700; padding:2px 0;")
            add(change)

    def _build_df_table(self, df, diff_cols_per_row: dict = None) -> QWidget:
        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 2, 0, 4)
        cv.setSpacing(4)

        toolbar = QWidget()
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        tb.addStretch()

        copy_sel_btn = QPushButton("⎘ Copy Selected")
        copy_all_btn = QPushButton("⎘ Copy All as CSV")
        for b in (copy_sel_btn, copy_all_btn):
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_MUTED}; border:1px solid {_BORDER};"
                f" border-radius:3px; font-size:11px; padding:0 8px;"
            )
        tb.addWidget(copy_sel_btn)
        tb.addWidget(copy_all_btn)
        cv.addWidget(toolbar)

        cols = list(df.columns)
        tbl  = QTableWidget(len(df), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tbl.setFixedHeight(min(36 * len(df) + 40, 320))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tbl.setContextMenuPolicy(Qt.CustomContextMenu)

        for r_i, (_, row) in enumerate(df.iterrows()):
            for c_i, col in enumerate(cols):
                val = str(row[col])
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if diff_cols_per_row:
                    info = diff_cols_per_row.get(r_i)
                    if info and col in info:
                        side, _ = info[col]
                        bg = QColor(_alpha(_FAIL_COLOR, "44")) if side == "orig" else QColor(_alpha(_PASS_COLOR, "44"))
                        item.setBackground(QBrush(bg))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)

        def _rows_to_csv(row_indices):
            lines = ["\t".join(cols)]
            for ri in sorted(row_indices):
                cells = [tbl.item(ri, ci).text() if tbl.item(ri, ci) else ""
                         for ci in range(len(cols))]
                lines.append("\t".join(cells))
            return "\n".join(lines)

        def _copy_selected():
            rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
            if rows:
                _copy_with_feedback(copy_sel_btn, _rows_to_csv(rows), "⎘ Copy Selected")

        def _copy_all():
            _copy_with_feedback(copy_all_btn, _rows_to_csv(range(tbl.rowCount())), "⎘ Copy All as CSV")

        copy_sel_btn.clicked.connect(_copy_selected)
        copy_all_btn.clicked.connect(_copy_all)

        def _context_menu(pos):
            menu = QMenu(tbl)
            menu.setStyleSheet(
                f"QMenu {{ background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" font-size:12px; }}"
                f" QMenu::item:selected {{ background:#0A84FF; color:#fff; }}"
            )
            sel_rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
            if sel_rows:
                act_copy_sel = menu.addAction(f"⎘  Copy {len(sel_rows)} selected row(s)")
                act_copy_sel.triggered.connect(_copy_selected)
            act_sel_all = menu.addAction("Select All")
            act_sel_all.triggered.connect(tbl.selectAll)
            act_copy_all = menu.addAction("⎘  Copy All as CSV")
            act_copy_all.triggered.connect(_copy_all)
            menu.exec(tbl.viewport().mapToGlobal(pos))

        tbl.customContextMenuRequested.connect(_context_menu)

        return container

    def _build_col_diff_table(self, col_diff_rows, common_cols) -> QWidget:
        flat_rows = []
        for dr in col_diff_rows:
            for col, (ov, pv) in dr.diffs.items():
                flat_rows.append((dr.row_idx, col, ov, pv))

        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 2, 0, 4)
        cv.setSpacing(4)

        toolbar = QWidget()
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        info_lbl = QLabel(
            f"<span style='color:{_MUTED}; font-size:11px;'>"
            f"Showing {len(flat_rows)} cell differences across {len(col_diff_rows)} rows"
            f" — cells highlighted: "
            f"<span style='color:{_FAIL_COLOR};'>■</span> Query 1  "
            f"<span style='color:{_PASS_COLOR};'>■</span> Query 2</span>"
        )
        info_lbl.setTextFormat(Qt.RichText)
        tb.addWidget(info_lbl, 1)
        copy_btn = QPushButton("⎘ Copy")
        copy_btn.setFixedHeight(22)
        copy_btn.setStyleSheet(
            f"background:{_PANEL_BG}; color:{_MUTED}; border:1px solid {_BORDER};"
            f" border-radius:3px; font-size:11px; padding:0 8px;"
        )
        tb.addWidget(copy_btn)
        cv.addWidget(toolbar)

        tbl = QTableWidget(len(flat_rows), 4)
        tbl.setHorizontalHeaderLabels(["Row #", "Column", "Query 1 Value", "Query 2 Value"])
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tbl.setFixedHeight(min(30 * len(flat_rows) + 40, 400))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tbl.setContextMenuPolicy(Qt.CustomContextMenu)

        for r_i, (row_no, col, ov, pv) in enumerate(flat_rows):
            for c_i, (val, bg) in enumerate([
                (str(row_no), None),
                (col, None),
                (ov, QColor(_alpha(_FAIL_COLOR, "44"))),
                (pv, QColor(_alpha(_PASS_COLOR, "44"))),
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if bg:
                    item.setBackground(QBrush(bg))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)

        def _copy_all_diff():
            lines = ["Row\tColumn\tQuery 1\tQuery 2"]
            for row_no, col, ov, pv in flat_rows:
                lines.append(f"{row_no}\t{col}\t{ov}\t{pv}")
            _copy_with_feedback(copy_btn, "\n".join(lines), "⎘ Copy")

        copy_btn.clicked.connect(_copy_all_diff)

        def _context_menu(pos):
            menu = QMenu(tbl)
            menu.setStyleSheet(
                f"QMenu {{ background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" font-size:12px; }}"
                f" QMenu::item:selected {{ background:#0A84FF; color:#fff; }}"
            )
            menu.addAction("⎘  Copy All Diffs").triggered.connect(_copy_all_diff)
            menu.addAction("Select All").triggered.connect(tbl.selectAll)
            menu.exec(tbl.viewport().mapToGlobal(pos))

        tbl.customContextMenuRequested.connect(_context_menu)
        return container

    def _build_explain_table(self, explain_rows, title: str) -> QWidget:
        _ACCESS_COLOR = {
            **{k: _PASS_COLOR for k in ("system", "const", "eq_ref", "ref")},
            **{k: _WARN_COLOR for k in ("range", "index_merge", "ref_or_null")},
            **{k: _FAIL_COLOR for k in ("index", "ALL")},
        }

        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(2)

        header = QLabel(title)
        header.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600; padding:2px 0;")
        cv.addWidget(header)

        if not explain_rows:
            lbl = QLabel("EXPLAIN not available for this query type.")
            lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
            cv.addWidget(lbl)
            return container

        cols = ["#", "Table", "Type", "Key", "Key Length", "Rows", "Filtered %", "Extra"]
        tbl = QTableWidget(len(explain_rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setFixedHeight(min(32 * len(explain_rows) + 40, 260))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        for r_i, er in enumerate(explain_rows):
            row_vals = [er.id, er.table, er.access_type, er.key,
                        er.key_len, er.rows, er.filtered, er.extra]
            for c_i, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if c_i == 2 and val:
                    color = _ACCESS_COLOR.get(val, _MUTED)
                    item.setForeground(QBrush(QColor(color)))
                if c_i == 2 and val == "ALL":
                    item.setBackground(QBrush(QColor(_alpha(_FAIL_COLOR, "33"))))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)
        return container


# ===========================================================================
# Top-level dialog
# ===========================================================================

class QueryAnalyzerDialog(QDialog):
    """Consolidated entry point: Cost & Profile + Compare Queries tabs."""

    def __init__(self, db_service, initial_query: str = "", initial_cost_detail: dict = None,
                 initial_profile_detail: dict = None, query_history=None,
                 history_entry_id: str = None, connection_name: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyze Query")
        self.setMinimumSize(1000, 720)
        self.resize(1100, 800)
        self.setModal(False)
        self.setStyleSheet(_SHARED_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._tabs = QTabWidget()
        self._cost_tab = _CostProfileTab(db_service, initial_query=initial_query,
                                          initial_cost_detail=initial_cost_detail,
                                          initial_profile_detail=initial_profile_detail,
                                          query_history=query_history,
                                          history_entry_id=history_entry_id,
                                          connection_name=connection_name)
        self._compare_tab = _CompareQueriesTab(db_service, initial_query=initial_query)
        self._tabs.addTab(self._cost_tab, "Cost && Profile")
        self._tabs.addTab(self._compare_tab, "Compare Queries")
        layout.addWidget(self._tabs)

    def show_cost_tab(self):
        """Focus the Cost & Profile tab — used when opened from the
        status-bar cost badge rather than the Database menu."""
        self._tabs.setCurrentWidget(self._cost_tab)
