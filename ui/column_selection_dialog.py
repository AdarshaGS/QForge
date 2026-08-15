from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QScrollArea,
    QWidget,
    QDialogButtonBox,
)


class ColumnSelectionDialog(QDialog):
    """Lets the user pick which columns to include before exporting
    (issue #142's "Export Table with Column Selection"). Styled after
    ExportScopeDialog's table checklist."""

    def __init__(self, columns: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Columns to Export")
        self.setMinimumWidth(320)

        self._checks: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Columns:"))

        select_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        clear_all_btn = QPushButton("Clear All")
        select_row.addWidget(select_all_btn)
        select_row.addWidget(clear_all_btn)
        select_row.addStretch()
        layout.addLayout(select_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.setSpacing(2)
        for col in columns:
            cb = QCheckBox(col)
            cb.setChecked(True)
            self._checks[col] = cb
            list_layout.addWidget(cb)
        list_layout.addStretch()
        scroll.setWidget(list_widget)
        layout.addWidget(scroll)

        select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        clear_all_btn.clicked.connect(lambda: self._set_all_checked(False))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool):
        for cb in self._checks.values():
            cb.setChecked(checked)

    def selected_columns(self) -> list[str]:
        return [name for name, cb in self._checks.items() if cb.isChecked()]
