from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QGroupBox,
    QSpinBox,
    QScrollArea,
    QTabBar,
    QWidget,
    QDialogButtonBox,
)

_COLUMNS = (("structure", "S", "Structure"), ("content", "C", "Content"), ("drop", "D", "Drop"))
_DEFAULTS = {"structure": True, "content": True, "drop": False}

# Issue #159: format tabs. Each format only cares about a subset of the
# S/C/D grid and the advanced-options panel — both widgets stay shared
# across tabs (not rebuilt per tab), just enabled/disabled per format.
_FORMATS = ("sql", "csv", "xml", "dot")
_FORMAT_LABELS = ("SQL", "CSV", "XML", "Dot")
_FORMAT_COLUMNS = {
    "sql": {"structure", "content", "drop"},
    "csv": {"content"},
    "xml": {"content"},
    "dot": {"structure"},  # Dot diagrams schema, not rows — reuses "Structure" as "include"
}
_FORMAT_ADVANCED = {
    "sql": {"blob_hex", "bom", "gzip", "batch", "auto_increment", "strip_generated"},
    "csv": {"blob_hex", "bom"},
    "xml": {"blob_hex", "bom"},
    "dot": {"gzip"},
}
_ADVANCED_DEFAULTS = {
    "blob_hex": True, "bom": False, "gzip": False, "batch": False,
    "auto_increment": True, "strip_generated": False,
}


class ExportScopeDialog(QDialog):
    """Per-table Structure/Content/Drop export grid, format tabs (SQL/CSV/
    XML/Dot — issue #159), and an advanced-options panel (issue #157,
    replacing the single global structure/data/both radio group from issue
    #39). Reused by both ConnectionPanel.export_database() and
    ._export_table() — for a single table the grid still shows one row.

    *dialect* ('mysql' | 'postgresql' | 'sqlite') disables the two
    dialect-limited toggles — "include auto-increment value" only means
    anything on MySQL's DDL text, "exclude generated columns from CREATE
    TABLE" only on MySQL/SQLite, whose DDL text can actually contain a
    GENERATED clause to strip (issue #160)."""

    def __init__(self, tables: list[str], dialect: str = "mysql", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Options")
        self.setMinimumWidth(480)

        self._dialect = dialect
        self._dialect_supports = {
            "auto_increment": dialect == "mysql",
            "strip_generated": dialect in ("mysql", "sqlite"),
        }

        self._table_checks: dict[str, dict[str, QCheckBox]] = {}
        self._column_widgets: dict[str, list] = {key: [] for key, _, _ in _COLUMNS}
        layout = QVBoxLayout(self)

        self._tab_bar = QTabBar()
        for label in _FORMAT_LABELS:
            self._tab_bar.addTab(label)
        self._tab_bar.currentChanged.connect(self._on_format_changed)
        layout.addWidget(self._tab_bar)

        layout.addWidget(QLabel("Tables:"))
        layout.addWidget(self._build_grid(tables))
        layout.addWidget(self._build_advanced_panel())

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._on_format_changed(0)

    def _build_grid(self, tables: list[str]) -> QWidget:
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setSpacing(4)

        grid.addWidget(QLabel("<b>Table</b>"), 0, 0)
        for col, (key, short, full) in enumerate(_COLUMNS, start=1):
            header = QLabel(f"<b>{short}</b>")
            header.setToolTip(full)
            grid.addWidget(header, 0, col)

            toggles = QHBoxLayout()
            toggles.setSpacing(2)
            all_btn = QPushButton("✓")
            none_btn = QPushButton("–")
            all_btn.setToolTip(f"Check {full} for all tables")
            none_btn.setToolTip(f"Uncheck {full} for all tables")
            for btn in (all_btn, none_btn):
                btn.setFlat(True)
                btn.setFixedWidth(22)
                btn.setStyleSheet("padding: 0; font-size: 12px; font-weight: bold;")
            toggles.addWidget(all_btn)
            toggles.addWidget(none_btn)
            grid.addLayout(toggles, 1, col)
            all_btn.clicked.connect(lambda _checked=False, k=key: self._set_column(k, True))
            none_btn.clicked.connect(lambda _checked=False, k=key: self._set_column(k, False))
            self._column_widgets[key].extend([all_btn, none_btn])

        for row, table in enumerate(tables, start=2):
            grid.addWidget(QLabel(table), row, 0)
            checks = {}
            for col, (key, _short, _full) in enumerate(_COLUMNS, start=1):
                cb = QCheckBox()
                cb.setChecked(_DEFAULTS[key])
                grid.addWidget(cb, row, col)
                checks[key] = cb
                self._column_widgets[key].append(cb)
            self._table_checks[table] = checks

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(260)
        scroll.setWidget(grid_widget)
        return scroll

    def _set_column(self, key: str, checked: bool):
        for checks in self._table_checks.values():
            checks[key].setChecked(checked)

    def _build_advanced_panel(self) -> QWidget:
        box = QGroupBox("Advanced")
        v = QVBoxLayout(box)

        self._blob_hex_cb = QCheckBox("Encode BLOB/binary columns as hex")
        self._blob_hex_cb.setChecked(True)
        v.addWidget(self._blob_hex_cb)

        self._bom_cb = QCheckBox("Write UTF-8 BOM")
        v.addWidget(self._bom_cb)

        self._gzip_cb = QCheckBox("Compress output (.gz)")
        v.addWidget(self._gzip_cb)

        batch_row = QHBoxLayout()
        self._batch_cb = QCheckBox("New INSERT statement every")
        self._batch_spin = QSpinBox()
        self._batch_spin.setRange(1, 1_000_000)
        self._batch_spin.setValue(64)
        self._batch_spin.setEnabled(False)
        self._batch_cb.toggled.connect(self._batch_spin.setEnabled)
        batch_row.addWidget(self._batch_cb)
        batch_row.addWidget(self._batch_spin)
        batch_row.addWidget(QLabel("KiB"))
        batch_row.addStretch()
        v.addLayout(batch_row)

        self._auto_increment_cb = QCheckBox("Include auto-increment starting value")
        self._auto_increment_cb.setChecked(True)
        if not self._dialect_supports["auto_increment"]:
            self._auto_increment_cb.setToolTip(f"Not supported on {self._dialect}")
        v.addWidget(self._auto_increment_cb)

        self._strip_generated_cb = QCheckBox("Exclude generated columns from CREATE TABLE")
        if not self._dialect_supports["strip_generated"]:
            self._strip_generated_cb.setToolTip(f"Not supported on {self._dialect}")
        v.addWidget(self._strip_generated_cb)

        self._advanced_widgets = {
            "blob_hex": [self._blob_hex_cb],
            "bom": [self._bom_cb],
            "gzip": [self._gzip_cb],
            "batch": [self._batch_cb, self._batch_spin],
            "auto_increment": [self._auto_increment_cb],
            "strip_generated": [self._strip_generated_cb],
        }
        return box

    def _on_format_changed(self, index: int):
        fmt = _FORMATS[index]
        relevant_columns = _FORMAT_COLUMNS[fmt]
        for key, widgets in self._column_widgets.items():
            enabled = key in relevant_columns
            for w in widgets:
                w.setEnabled(enabled)
            if not enabled:
                self._set_column(key, False)
            else:
                self._set_column(key, _DEFAULTS[key])

        relevant_advanced = _FORMAT_ADVANCED[fmt]
        for key, widgets in self._advanced_widgets.items():
            enabled = key in relevant_advanced and self._dialect_supports.get(key, True)
            for w in widgets:
                w.setEnabled(enabled)
            widgets[0].setChecked(enabled and _ADVANCED_DEFAULTS[key])

    def export_format(self) -> str:
        """'sql' | 'csv' | 'xml' | 'dot'"""
        return _FORMATS[self._tab_bar.currentIndex()]

    def table_options(self) -> dict[str, dict[str, bool]]:
        """{table: {"structure": bool, "content": bool, "drop": bool}}.
        A table with all three boxes unchecked is excluded entirely."""
        result = {}
        for table, checks in self._table_checks.items():
            opts = {key: cb.isChecked() for key, cb in checks.items()}
            if any(opts.values()):
                result[table] = opts
        return result

    def blob_as_hex(self) -> bool:
        return self._blob_hex_cb.isChecked()

    def use_bom(self) -> bool:
        return self._bom_cb.isChecked()

    def gzip_output(self) -> bool:
        return self._gzip_cb.isChecked()

    def batch_kib(self):
        """KiB threshold for multi-row INSERTs, or None (one row per
        INSERT — today's behavior) when the toggle is off."""
        return self._batch_spin.value() if self._batch_cb.isChecked() else None

    def include_auto_increment(self) -> bool:
        """False strips MySQL's `AUTO_INCREMENT=N` clause from the exported
        DDL text (issue #160) — a no-op the dialog never surfaces on
        Postgres/SQLite, which have no such clause in the first place."""
        return self._auto_increment_cb.isChecked()

    def strip_generated_columns(self) -> bool:
        """True strips `GENERATED ALWAYS AS (...)` clauses from the
        exported CREATE TABLE text (issue #160). Generated columns are
        always excluded from INSERT data regardless of this toggle — they
        can't appear in an INSERT column list to begin with."""
        return self._strip_generated_cb.isChecked()
