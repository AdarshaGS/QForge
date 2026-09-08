"""
Scrollable, comfortably-spaced confirmation dialog for "about to run this SQL"
prompts. Replaces plain QMessageBox.question(...) for SQL previews, which has
no scroll area and forces the dialog to balloon to fit however much SQL is
passed in (e.g. multi-row INSERTs from mock data generation).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QDialogButtonBox,
)
from PySide6.QtGui import QFont


def confirm_sql(parent, title: str, sql: str, extra_text: str = "") -> bool:
    """Show a Yes/No confirmation dialog previewing `sql`. Returns True if the
    user confirmed. `extra_text`, if given, is shown above the SQL as a plain
    (wrapping) label, e.g. an impact-analysis warning."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(700, 500)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(12)

    prompt = QLabel("Execute the following SQL?")
    prompt_font = prompt.font()
    prompt_font.setBold(True)
    prompt.setFont(prompt_font)
    layout.addWidget(prompt)

    if extra_text:
        warning = QLabel(extra_text)
        warning.setWordWrap(True)
        layout.addWidget(warning)

    preview = "\n\n".join(
        stmt.strip() for stmt in sql.strip().split(";\n") if stmt.strip()
    )
    text_edit = QPlainTextEdit(preview)
    text_edit.setReadOnly(True)
    mono = QFont("Menlo, Monaco, Consolas, monospace")
    mono.setStyleHint(QFont.Monospace)
    mono.setPointSize(12)
    text_edit.setFont(mono)
    text_edit.setStyleSheet("QPlainTextEdit { padding: 10px; }")
    layout.addWidget(text_edit, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.Yes | QDialogButtonBox.No)
    buttons.button(QDialogButtonBox.Yes).setDefault(True)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    return dialog.exec() == QDialog.Accepted
