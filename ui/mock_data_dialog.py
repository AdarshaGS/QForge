"""Mock Data Generator dialog (issue #77). Per-column generator
configuration, a live Preview grid + SQL tab, and Copy/Insert actions.
Pure UI — the actual generation logic lives in
services/mock_data_generator.py, and environment/read-only safety gating
happens in ui/connection_panel.py before this dialog is even opened (see
ui/query_guard_dialog.py's mock_data_generation_allowed).

This dialog only ever returns SQL text via get_sql(); the caller
(ConnectionPanel.show_mock_data_generator) owns guarding, confirming, and
actually executing it against the database — same convention as
StructureEditorDialog.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from services import mock_data_generator as gen

_PREVIEW_STYLE = (
    "font-family: monospace; font-size: 12px; "
    "background: #1e1e1e; color: #e5e5ea; border: 1px solid #3a3a3c;"
)

# Row count is capped well below the DataFrame/SQL-build cost becoming
# noticeable, and the Preview grid — QTableWidgetItem construction is the
# real bottleneck, not generation — never renders more than this many rows
# regardless of how many were generated (issue #180).
_MAX_ROW_COUNT = 10_000
_PREVIEW_ROW_CAP = 500

# Generators with no configurable options — the Options button is disabled.
_NO_OPTIONS = {"uuid", "boolean", "email", "first_name", "last_name",
               "full_name", "phone", "address", "city", "company", "job",
               "lorem_text", "null", "foreign_key", "omit"}


class _OptionsDialog(QDialog):
    """Small inline popup editing the options dict for one column's
    currently-selected generator. Only shows fields relevant to that
    generator — the rest of the app follows the same "only show what
    applies" pattern in ExportScopeDialog's per-format advanced panel."""

    def __init__(self, generator: str, options: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Options — {gen.GENERATOR_LABELS.get(generator, generator)}")
        self._generator = generator
        self._fields: dict[str, QWidget] = {}
        layout = QVBoxLayout(self)

        if generator in ("integer", "float"):
            row = QHBoxLayout()
            row.addWidget(QLabel("Min:"))
            min_spin = QDoubleSpinBox() if generator == "float" else QSpinBox()
            min_spin.setRange(-1_000_000_000, 1_000_000_000)
            min_spin.setValue(options.get("min", 0 if generator == "float" else 1))
            row.addWidget(min_spin)
            row.addWidget(QLabel("Max:"))
            max_spin = QDoubleSpinBox() if generator == "float" else QSpinBox()
            max_spin.setRange(-1_000_000_000, 1_000_000_000)
            max_spin.setValue(options.get("max", 1000 if generator == "float" else 100_000))
            row.addWidget(max_spin)
            layout.addLayout(row)
            self._fields["min"] = min_spin
            self._fields["max"] = max_spin
            if generator == "float":
                dec_row = QHBoxLayout()
                dec_row.addWidget(QLabel("Decimals:"))
                dec_spin = QSpinBox()
                dec_spin.setRange(0, 10)
                dec_spin.setValue(options.get("decimals", 2))
                dec_row.addWidget(dec_spin)
                layout.addLayout(dec_row)
                self._fields["decimals"] = dec_spin
        elif generator == "string":
            row = QHBoxLayout()
            row.addWidget(QLabel("Length:"))
            length_spin = QSpinBox()
            length_spin.setRange(1, 4000)
            length_spin.setValue(options.get("length", 10))
            row.addWidget(length_spin)
            layout.addLayout(row)
            self._fields["length"] = length_spin
        elif generator in ("date", "datetime"):
            row = QHBoxLayout()
            row.addWidget(QLabel("Start (YYYY-MM-DD):"))
            start_edit = QLineEdit(options.get("start", "2020-01-01"))
            row.addWidget(start_edit)
            layout.addLayout(row)
            row2 = QHBoxLayout()
            row2.addWidget(QLabel("End (YYYY-MM-DD):"))
            from datetime import date as _date
            end_edit = QLineEdit(options.get("end", _date.today().isoformat()))
            row2.addWidget(end_edit)
            layout.addLayout(row2)
            self._fields["start"] = start_edit
            self._fields["end"] = end_edit
        elif generator == "custom_pattern":
            layout.addWidget(QLabel(
                "Pattern tokens: {seq} 1-based row number, {seq0} 0-based, "
                "{uuid}, {random_int:MIN-MAX}"
            ))
            pattern_edit = QLineEdit(options.get("pattern", "ITEM-{seq}"))
            layout.addWidget(pattern_edit)
            self._fields["pattern"] = pattern_edit
        else:
            layout.addWidget(QLabel("This generator has no options."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        result = {}
        for key, widget in self._fields.items():
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                result[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()
        return result


class MockDataDialog(QDialog):
    def __init__(self, table_name: str, columns: list[dict],
                 primary_keys: list[str], foreign_keys: list[dict],
                 generated_columns: list[str], dialect: str = "mysql",
                 fk_sampler=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Generate Mock Data — {table_name}")
        self.setMinimumSize(700, 560)

        self._table_name = table_name
        self._dialect = dialect
        self._fk_sampler = fk_sampler
        self._fk_pool_cache: dict[tuple, list] = {}
        self._last_sql = ""
        # Include state to restore for a column when its generator is
        # switched away from "omit" — set only while "omit" is active, so a
        # column the user had already excluded for its own reason (not
        # because of "omit") comes back excluded rather than forced on.
        self._pre_omit_include: dict[str, bool] = {}

        generated_set = set(generated_columns)
        self._columns = [c for c in columns if c["Field"] not in generated_set]
        self._fk_map = {fk["column"]: fk for fk in foreign_keys}
        lone_pk = primary_keys[0] if len(primary_keys) == 1 else None

        self._specs: dict[str, gen.ColumnSpec] = {}
        for col in self._columns:
            field_name = col["Field"]
            is_pk = field_name == lone_pk
            is_fk = field_name in self._fk_map
            generator = gen.infer_generator(col, is_pk=is_pk, is_fk=is_fk)
            self._specs[field_name] = gen.ColumnSpec(
                generator=generator, include=generator != "omit",
            )

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Rows to generate:"))
        self._row_count_spin = QSpinBox()
        self._row_count_spin.setRange(1, _MAX_ROW_COUNT)
        self._row_count_spin.setValue(10)
        top_row.addWidget(self._row_count_spin)
        top_row.addStretch()
        layout.addLayout(top_row)

        layout.addWidget(self._build_column_grid())

        self._warning_label = QLabel("")
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet("color: #c9622a;")
        layout.addWidget(self._warning_label)

        self._tabs = QTabWidget()
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tabs.addTab(self._preview_table, "Preview")
        self._sql_view = QTextEdit()
        self._sql_view.setReadOnly(True)
        self._sql_view.setStyleSheet(_PREVIEW_STYLE)
        self._tabs.addTab(self._sql_view, "SQL")
        layout.addWidget(self._tabs, stretch=1)

        action_row = QHBoxLayout()
        regen_btn = QPushButton("Regenerate")
        regen_btn.clicked.connect(self._regenerate)
        action_row.addWidget(regen_btn)
        copy_btn = QPushButton("Copy SQL")
        copy_btn.clicked.connect(self._copy_sql)
        action_row.addWidget(copy_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Insert into Database…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._regenerate()

    # ─── Column config grid ────────────────────────────────────────────────

    def _build_column_grid(self) -> QTableWidget:
        table = QTableWidget(len(self._columns), 6)
        table.setHorizontalHeaderLabels(["Column", "Type", "Include", "Generator", "Null %", "Options"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        table.setMaximumHeight(240)

        self._include_checks: dict[str, QCheckBox] = {}
        self._generator_combos: dict[str, QComboBox] = {}
        self._null_spins: dict[str, QSpinBox] = {}
        self._options_buttons: dict[str, QPushButton] = {}

        for row, col in enumerate(self._columns):
            name = col["Field"]
            spec = self._specs[name]
            not_null = col.get("Null") == "NO"

            table.setItem(row, 0, QTableWidgetItem(name))
            table.setItem(row, 1, QTableWidgetItem(str(col.get("Type", ""))))

            include_cb = QCheckBox()
            include_cb.setChecked(spec.include)
            include_cb.setEnabled(spec.generator != "omit")
            include_cb.toggled.connect(lambda checked, n=name: self._set_include(n, checked))
            table.setCellWidget(row, 2, include_cb)
            self._include_checks[name] = include_cb

            combo = QComboBox()
            for gid in gen.GENERATORS:
                combo.addItem(gen.GENERATOR_LABELS[gid], gid)
            combo.setCurrentIndex(combo.findData(spec.generator))
            combo.currentIndexChanged.connect(lambda _idx, n=name, c=combo: self._set_generator(n, c.currentData()))
            table.setCellWidget(row, 3, combo)
            self._generator_combos[name] = combo

            null_spin = QSpinBox()
            null_spin.setRange(0, 100)
            null_spin.setSuffix("%")
            null_spin.setEnabled(not not_null)
            null_spin.valueChanged.connect(lambda v, n=name: self._set_null_rate(n, v))
            table.setCellWidget(row, 4, null_spin)
            self._null_spins[name] = null_spin

            options_btn = QPushButton("…")
            options_btn.setEnabled(spec.generator not in _NO_OPTIONS)
            options_btn.clicked.connect(lambda _checked=False, n=name: self._edit_options(n))
            table.setCellWidget(row, 5, options_btn)
            self._options_buttons[name] = options_btn
            combo.currentIndexChanged.connect(
                lambda _idx, n=name, c=combo, b=options_btn: b.setEnabled(c.currentData() not in _NO_OPTIONS)
            )

        return table

    def _set_include(self, name: str, checked: bool):
        self._specs[name].include = checked

    def _set_generator(self, name: str, generator: str):
        spec = self._specs[name]
        was_omit = spec.generator == "omit"
        spec.generator = generator
        spec.options = {}

        checkbox = self._include_checks.get(name)
        if generator == "omit":
            # "Omit" means "not in the INSERT column list at all" — force
            # Include off and lock the checkbox so it can't be re-checked
            # while "omit" is selected (issue #180: switching to "omit"
            # previously left Include untouched, so the column stayed in
            # the statement with an explicit NULL instead of being dropped).
            if not was_omit:
                self._pre_omit_include[name] = spec.include
            spec.include = False
            if checkbox is not None:
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
                checkbox.setEnabled(False)
        elif was_omit:
            spec.include = self._pre_omit_include.pop(name, True)
            if checkbox is not None:
                checkbox.setEnabled(True)
                checkbox.blockSignals(True)
                checkbox.setChecked(spec.include)
                checkbox.blockSignals(False)

    def _set_null_rate(self, name: str, percent: int):
        self._specs[name].null_rate = percent / 100.0

    def _edit_options(self, name: str):
        spec = self._specs[name]
        if spec.generator in _NO_OPTIONS:
            return
        dlg = _OptionsDialog(spec.generator, spec.options, parent=self)
        if dlg.exec():
            spec.options = dlg.options()

    # ─── Generate / preview ────────────────────────────────────────────────

    def _fk_pool(self, name: str) -> list:
        fk = self._fk_map.get(name)
        if not fk or not self._fk_sampler:
            return []
        key = (fk["ref_table"], fk["ref_column"])
        if key not in self._fk_pool_cache:
            try:
                self._fk_pool_cache[key] = self._fk_sampler(fk["ref_table"], fk["ref_column"])
            except Exception:
                self._fk_pool_cache[key] = []
        return self._fk_pool_cache[key]

    def _regenerate(self):
        row_count = self._row_count_spin.value()
        fk_pools = {
            name: self._fk_pool(name)
            for name, spec in self._specs.items()
            if spec.generator == "foreign_key" and spec.include
        }

        warnings = []
        for col in self._columns:
            name = col["Field"]
            spec = self._specs[name]
            if not spec.include:
                continue
            not_null = col.get("Null") == "NO"
            if spec.generator == "foreign_key" and not fk_pools.get(name):
                warnings.append(
                    f"'{name}' references an empty (or unreachable) table — "
                    f"generated value will be NULL"
                    + (" and the insert will fail (NOT NULL)" if not_null else "")
                )
            elif spec.generator == "null" and not_null:
                warnings.append(
                    f"'{name}' is NOT NULL but set to \"Always NULL\" — the insert will fail"
                )
        self._warning_label.setText("⚠ " + "; ".join(warnings) if warnings else "")

        df = gen.generate_dataframe(self._columns, row_count, self._specs, fk_pools)
        self._populate_preview(df)
        self._last_sql = gen.build_insert_sql(df, self._table_name, dialect=self._dialect)
        self._sql_view.setPlainText(self._last_sql or "-- No columns selected to insert.")

    def _populate_preview(self, df):
        # QTableWidgetItem construction, not DataFrame generation, is what
        # actually freezes the UI at high row counts (issue #180) — capping
        # how many rows the grid ever renders keeps Regenerate responsive
        # regardless of the requested row count. The full row count still
        # goes into the generated SQL; only the on-screen preview is capped.
        shown = min(len(df), _PREVIEW_ROW_CAP)
        preview_df = df.head(shown)

        self._preview_table.clear()
        self._preview_table.setColumnCount(len(preview_df.columns))
        self._preview_table.setHorizontalHeaderLabels([str(c) for c in preview_df.columns])
        self._preview_table.setRowCount(shown)
        for row in range(shown):
            for col in range(len(preview_df.columns)):
                value = preview_df.iloc[row, col]
                text = "" if value is None else str(value)
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self._preview_table.setItem(row, col, item)

        tab_label = f"Preview (first {shown:,} of {len(df):,})" if shown < len(df) else "Preview"
        self._tabs.setTabText(0, tab_label)

    def _copy_sql(self):
        QApplication.clipboard().setText(self._last_sql)

    def get_sql(self) -> str:
        return self._last_sql
