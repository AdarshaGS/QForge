"""Preferences/Settings dialog (issue #264).

Minimal to start: houses the theme toggle previously living as a bare
View-menu item. The menu action that opens this carries
QAction.MenuRole.PreferencesRole so Qt relocates it into the app menu on
macOS ("QForge > Settings…") instead of leaving it in whatever menu it's
nominally attached to.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QGroupBox,
    QDialogButtonBox,
)


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

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _on_theme_changed(self, text: str):
        target = "dark" if text == "Dark" else "light"
        if target != self.main_window.current_theme:
            self.main_window.toggle_theme()
