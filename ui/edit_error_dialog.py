"""Structured error dialog for failed grid edits (issue #143).

Replaces ui/table_view_widget.py's commit_changes() plain QMessageBox.warning,
which showed the raw database exception (constraint names and all) as the
primary message, with: a human-readable summary, per-failure classification
reusing the same title/hint engine ui/sql_tab.py's query-error card uses (so
a foreign-key violation reads the same whether it came from running a query
or saving a grid edit — see utils/sql_errors.py), and a Details section
(collapsed by default) with the full raw error text plus a Copy Details
button, for support/debugging.
"""
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from utils.sql_errors import sql_error_hint, sql_error_title

_DETAILS_STYLE = (
    "font-family: Menlo, Consolas, monospace; font-size: 12px; "
    "background: #1e1e1e; color: #e5e5ea; border: 1px solid #3a3a3c; border-radius: 4px;"
)
_FALLBACK_HINT = "This change was rejected by the database — see Details for the exact error."


def show_save_errors(parent, success_count: int, failures: list) -> None:
    """*failures*: list of {"kind": "DELETE"/"UPDATE"/"INSERT", "sql": str,
    "error": str}. Only meant to be called when *failures* is non-empty."""
    total = success_count + len(failures)
    dlg = QDialog(parent)
    dlg.setWindowTitle("Save Errors")
    dlg.setMinimumWidth(520)
    layout = QVBoxLayout(dlg)

    heading = QLabel("Some changes could not be saved")
    heading.setStyleSheet("font-size: 15px; font-weight: 600;")
    layout.addWidget(heading)

    plural = "s" if total != 1 else ""
    counts = QLabel(f"Saved {success_count} of {total} change{plural}. {len(failures)} failed.")
    counts.setStyleSheet("color: #8e8e93; font-size: 12px;")
    layout.addWidget(counts)

    # Group identical (title, hint) pairs — e.g. 50 rows failing the same FK
    # check shouldn't produce 50 near-identical cards. Every individual raw
    # error still lives in Details below.
    groups: dict = {}
    order: list = []
    for f in failures:
        title = sql_error_title(f["error"])
        hint = sql_error_hint(f["error"]) or _FALLBACK_HINT
        key = (title, hint)
        groups[key] = groups.get(key, 0) + 1
        if key not in order:
            order.append(key)

    cards = QWidget()
    cards_layout = QVBoxLayout(cards)
    cards_layout.setContentsMargins(0, 8, 0, 0)
    cards_layout.setSpacing(8)
    for title, hint in order:
        count = groups[(title, hint)]
        card = QWidget()
        card.setObjectName("errorTypeCard")
        card.setStyleSheet(
            "QWidget#errorTypeCard { background: #3a1a1a; border-radius: 6px; "
            "border-left: 3px solid #f48771; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(4)

        title_lbl = QLabel(title if count == 1 else f"{title} (×{count})")
        title_lbl.setStyleSheet("color: #f48771; font-size: 13px; font-weight: 600;")
        card_layout.addWidget(title_lbl)

        hint_lbl = QLabel(hint)
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet("color: #e5c6c1; font-size: 12px;")
        card_layout.addWidget(hint_lbl)

        cards_layout.addWidget(card)
    layout.addWidget(cards)

    details_text = "\n\n".join(f"[{f['kind']}] {f['sql']}\n{f['error']}" for f in failures)

    toggle_btn = QPushButton("▸ Details")
    toggle_btn.setCheckable(True)
    toggle_btn.setStyleSheet(
        "QPushButton { text-align: left; border: none; background: transparent; "
        "color: #8e8e93; font-size: 12px; padding: 4px 0; }"
        "QPushButton:hover, QPushButton:checked, QPushButton:pressed { "
        "background: transparent; color: #e5e5ea; }"
    )
    layout.addWidget(toggle_btn)

    details_box = QPlainTextEdit(details_text)
    details_box.setReadOnly(True)
    details_box.setStyleSheet(_DETAILS_STYLE)
    details_box.setFixedHeight(160)
    details_box.hide()
    layout.addWidget(details_box)

    def _toggle(checked: bool):
        details_box.setVisible(checked)
        toggle_btn.setText(("▾" if checked else "▸") + " Details")
    toggle_btn.toggled.connect(_toggle)

    button_row = QHBoxLayout()
    copy_btn = QPushButton("Copy Details")
    copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(details_text))
    button_row.addWidget(copy_btn)
    button_row.addStretch()
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    button_row.addWidget(buttons)
    layout.addLayout(button_row)

    dlg.exec()
