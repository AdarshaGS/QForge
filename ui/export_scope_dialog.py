from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QScrollArea,
    QWidget,
    QDialogButtonBox,
)


class ExportScopeDialog(QDialog):
    """Lets the user choose which tables and how much of each (structure
    only / data only / structure + data) to include in a database or table
    export (issue #39). Reused by both ConnectionPanel.export_database()
    and ._export_table() — for a single table the table checklist is
    skipped and only the content choice is shown."""

    def __init__(self, tables: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        self.setMinimumWidth(360)

        self._table_checks: dict[str, QCheckBox] = {}
        layout = QVBoxLayout(self)

        if len(tables) > 1:
            layout.addWidget(QLabel("Tables:"))

            select_row = QHBoxLayout()
            select_all_btn = QPushButton("Select All")
            clear_all_btn = QPushButton("Clear All")
            select_row.addWidget(select_all_btn)
            select_row.addWidget(clear_all_btn)
            select_row.addStretch()
            layout.addLayout(select_row)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(220)
            list_widget = QWidget()
            list_layout = QVBoxLayout(list_widget)
            list_layout.setSpacing(2)
            for table in tables:
                cb = QCheckBox(table)
                cb.setChecked(True)
                self._table_checks[table] = cb
                list_layout.addWidget(cb)
            list_layout.addStretch()
            scroll.setWidget(list_widget)
            layout.addWidget(scroll)

            select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
            clear_all_btn.clicked.connect(lambda: self._set_all_checked(False))
        else:
            # Single-table export: the table is implicit, nothing to pick.
            self._table_checks[tables[0]] = None  # type: ignore[assignment]

        layout.addWidget(QLabel("Include:"))
        self._content_group = QButtonGroup(self)
        self._structure_radio = QRadioButton("Structure only (schema/DDL)")
        self._data_radio = QRadioButton("Data only")
        self._both_radio = QRadioButton("Structure + Data")
        self._data_radio.setChecked(True)
        for i, rb in enumerate((self._structure_radio, self._data_radio, self._both_radio)):
            self._content_group.addButton(rb, i)
            layout.addWidget(rb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all_checked(self, checked: bool):
        for cb in self._table_checks.values():
            cb.setChecked(checked)

    def selected_tables(self) -> list[str]:
        return [name for name, cb in self._table_checks.items()
                if cb is None or cb.isChecked()]

    def content_mode(self) -> str:
        """'structure' | 'data' | 'both'"""
        if self._structure_radio.isChecked():
            return "structure"
        if self._both_radio.isChecked():
            return "both"
        return "data"
