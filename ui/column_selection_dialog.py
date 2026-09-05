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
    """Lets the user pick a subset of columns via a checklist. Originally
    built for "Export Table with Column Selection" (issue #142), styled
    after ExportScopeDialog's table checklist; also used for the data
    grid's "Manage Columns" visibility toggle (issue #252) via the
    *checked_columns*/*title*/*label* params below."""

    def __init__(self, columns: list[str], parent=None, checked_columns=None,
                 title: str = "Select Columns to Export", label: str = "Columns:",
                 disabled_columns=None, disabled_tooltip: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)
        # None (the export call site's default) means "everything starts
        # checked" — checked_columns is only meaningful as an inclusion
        # set once a caller passes one explicitly.
        checked_columns = set(columns) if checked_columns is None else set(checked_columns)
        disabled_columns = set(disabled_columns or ())

        self._checks: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))

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
            cb.setChecked(col in checked_columns)
            if col in disabled_columns:
                cb.setEnabled(False)
                if disabled_tooltip:
                    cb.setToolTip(disabled_tooltip)
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
