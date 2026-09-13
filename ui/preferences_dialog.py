"""Preferences/Settings dialog (issue #264).

Minimal to start: houses the theme toggle previously living as a bare
View-menu item. The menu action that opens this carries
QAction.MenuRole.PreferencesRole so Qt relocates it into the app menu on
macOS ("QForge > Settings…") instead of leaving it in whatever menu it's
nominally attached to.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox,
    QCheckBox, QDialogButtonBox,
)

from services import preferences
from ui.ai_availability_widget import AiAvailabilityWidget


class PreferencesDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        appearance_group = QGroupBox("Appearance")
        appearance_layout = QHBoxLayout(appearance_group)
        appearance_layout.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Dark", "Light"])
        self.theme_combo.setCurrentText(
            "Dark" if main_window.current_theme == "dark" else "Light")
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        appearance_layout.addWidget(self.theme_combo)
        appearance_layout.addStretch()
        layout.addWidget(appearance_group)

        ai_group = QGroupBox("AI Assistance")
        ai_layout = QVBoxLayout(ai_group)

        self.ai_enabled_check = QCheckBox("Enable AI features (uses your local Claude Code CLI)")
        self.ai_enabled_check.setChecked(bool(preferences.get("ai.enabled", False)))
        self.ai_enabled_check.toggled.connect(self._on_ai_enabled_toggled)
        ai_layout.addWidget(self.ai_enabled_check)

        disclosure = QLabel(
            "QForge will send query text and limited schema details "
            "(table/column names, not row data) to Anthropic via your "
            "local `claude` CLI when you use an AI feature. Off by default."
        )
        disclosure.setWordWrap(True)
        disclosure.setStyleSheet("color: #8e8e93; font-size: 11px;")
        ai_layout.addWidget(disclosure)

        self.ai_availability_widget = AiAvailabilityWidget(compact=True)
        ai_layout.addWidget(self.ai_availability_widget)

        self.ai_proactive_check = QCheckBox(
            "Automatically suggest optimizations after running a query")
        self.ai_proactive_check.setToolTip(
            "Runs an extra AI call (uses your own Claude account/usage) "
            "after each read-only query you run, and shows a status-bar "
            "badge if it finds anything worth suggesting.")
        self.ai_proactive_check.setChecked(bool(preferences.get("ai.proactive_optimize", False)))
        self.ai_proactive_check.setEnabled(self.ai_enabled_check.isChecked())
        self.ai_proactive_check.toggled.connect(
            lambda v: preferences.set("ai.proactive_optimize", v))
        self.ai_enabled_check.toggled.connect(self.ai_proactive_check.setEnabled)
        ai_layout.addWidget(self.ai_proactive_check)

        layout.addWidget(ai_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_theme_changed(self, text: str):
        target = "dark" if text == "Dark" else "light"
        if target != self.main_window.current_theme:
            self.main_window.toggle_theme()

    def _on_ai_enabled_toggled(self, checked: bool):
        preferences.set("ai.enabled", checked)
        if checked and not preferences.get("ai.disclosure_shown", False):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "AI Assistance",
                "AI features send query text and limited schema details "
                "(table/column names, index names — never row data) to "
                "Anthropic, via your local Claude Code CLI and your own "
                "Claude account. Nothing else about your usage of QForge "
                "leaves your machine.\n\n"
                "You can turn this off again here at any time.",
            )
            preferences.set("ai.disclosure_shown", True)
