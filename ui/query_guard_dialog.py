"""
Query guard dialogs — shown before a write reaches the database when the
connection is read-only, or when a statement is classified as dangerous on
a Staging/Production connection (ai/load-context.md, Slices 2/3).

These are UX aids, not the enforcement mechanism: services/db_service.py's
own read-only guard is the backstop that can't be bypassed by a new or
forgotten call site. This module only ever shows what's about to happen and
lets the user cancel or (for destructive DDL) type the connection name to
proceed.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QLineEdit,
    QDialogButtonBox,
)

from ui.theme_manager import ThemeManager
from utils import environment


def _statement_preview(statements: list) -> str:
    return "\n\n".join(statements)


def _env_badge_label(env: str) -> QLabel:
    bg, text, border = ThemeManager.env_colors(env, is_dark=True)
    label = QLabel(environment.BADGE_LABELS[env])
    label.setStyleSheet(
        f"background: {bg}; color: {text}; border: 1px solid {border}; "
        "border-radius: 4px; padding: 2px 8px; font-weight: 600; font-size: 11px;"
    )
    return label


def _statement_preview_widget(statements: list) -> QTextEdit:
    preview = QTextEdit(_statement_preview(statements))
    preview.setReadOnly(True)
    preview.setMaximumHeight(140)
    preview.setStyleSheet(
        "font-family: monospace; font-size: 12px; "
        "background: #1e1e1e; color: #e5e5ea; border: 1px solid #3a3a3c;"
    )
    return preview


def _header_row(connection_name: str, env: str) -> QHBoxLayout:
    header = QHBoxLayout()
    name_label = QLabel(f"<b>{connection_name}</b>")
    header.addWidget(name_label)
    header.addWidget(_env_badge_label(env))
    header.addStretch()
    return header


def show_read_only_blocked(parent, connection_name: str, env: str, statements: list) -> None:
    """Informational only — this connection is read-only, nothing to confirm."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Blocked — Read-only Connection")
    dlg.setMinimumWidth(480)
    layout = QVBoxLayout(dlg)

    layout.addLayout(_header_row(connection_name, env))
    layout.addWidget(QLabel(
        "This connection is read-only, so QForge blocked the following "
        "statement before sending it to the database:"
    ))
    layout.addWidget(_statement_preview_widget(statements))
    hint = QLabel(
        "Turn off Read-only for this connection in the Connection Manager "
        "if you intend to make changes."
    )
    hint.setWordWrap(True)
    layout.addWidget(hint)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()


def show_dangerous_confirmation(parent, connection_name: str, env: str,
                                 statements: list, reasons: list,
                                 require_typed_name: bool = False) -> bool:
    """Shows the exact statement(s) and why they were flagged. Returns True
    only if the user explicitly confirms — when require_typed_name is set
    (destructive DDL: DROP/TRUNCATE), Confirm stays disabled until the
    connection's name is typed exactly."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Confirm — Flagged Statement")
    dlg.setMinimumWidth(520)
    layout = QVBoxLayout(dlg)

    layout.addLayout(_header_row(connection_name, env))
    layout.addWidget(QLabel("QForge flagged the following statement:"))
    layout.addWidget(_statement_preview_widget(statements))

    reasons_label = QLabel("Why: " + "; ".join(reasons))
    reasons_label.setWordWrap(True)
    layout.addWidget(reasons_label)

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    ok_btn.setText("Confirm")

    if require_typed_name:
        type_hint = QLabel(f'Type the connection name "{connection_name}" to confirm:')
        type_hint.setWordWrap(True)
        layout.addWidget(type_hint)
        name_input = QLineEdit()
        layout.addWidget(name_input)
        ok_btn.setEnabled(False)
        name_input.textChanged.connect(
            lambda text: ok_btn.setEnabled(text == connection_name)
        )

    layout.addWidget(buttons)

    result = {"confirmed": False}

    def _accept():
        result["confirmed"] = True
        dlg.accept()

    buttons.accepted.connect(_accept)
    buttons.rejected.connect(dlg.reject)
    dlg.exec()
    return result["confirmed"]
