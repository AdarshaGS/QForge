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
import copy

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from services import mock_data_generator as gen
from services.mock_data_generator import DependencyChain, TablePlan

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

# Issue #217 — below this, generation runs inline (sub-second, thread setup
# would be pure overhead); at/above it, generation moves to a background
# QThread with a progress indicator so a large run can't freeze the window.
_BACKGROUND_ROW_THRESHOLD = 1_000

# Generators with no configurable options — the Options button is disabled.
_NO_OPTIONS = {"uuid", "boolean", "email", "first_name", "last_name",
               "full_name", "phone", "address", "city", "company", "job",
               "lorem_text", "null", "foreign_key", "omit",
               "json_object", "array", "geometry"}


def _build_single_result(columns, row_count, specs, fk_pools, unique_columns, table_name, dialect):
    """Pure (no Qt) single-table generation step (issue #217) — the actual
    work `_GenerationWorker` runs off the UI thread. Kept as a standalone
    function so it's testable without a QThread/event loop."""
    df = gen.generate_dataframe(columns, row_count, specs, fk_pools, unique_columns=unique_columns)
    sql = gen.build_insert_sql(df, table_name, dialect=dialect)
    return {"df": df, "sql": sql}


def _build_chain_result(chain, plans, external_pool_fn, dialect):
    """Pure (no Qt) multi-table (issue #215) generation step for issue
    #217's background worker — see _build_single_result."""
    dataframes = gen.generate_chain_dataframes(chain, plans, external_pool_fn=external_pool_fn)
    sql_parts = []
    generated_pk_columns = []
    for table in chain.tables:
        df = dataframes.get(table)
        if df is None or df.empty:
            continue
        sql_parts.append(gen.build_insert_sql(df, table, dialect=dialect))
        plan = plans.get(table)
        if plan and len(plan.primary_keys) == 1:
            generated_pk_columns.append((table, plan.primary_keys[0]))
    sql = "\n\n".join(p for p in sql_parts if p)
    return {"dataframes": dataframes, "sql": sql, "generated_pk_columns": generated_pk_columns}


class _GenerationWorker(QObject):
    """Runs one of the pure functions above on a QThread (issue #217),
    following _QueryWorker's shape in ui/connection_panel.py — but with no
    DB connection of its own: any FK pool this needs was already resolved
    to a plain in-memory dict on the main thread before the thread starts
    (see MockDataDialog._regenerate_chain's prefetch step), so nothing
    here ever touches self.db_service or a live connection from a
    background thread."""
    done = Signal(dict)
    errored = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as ex:
            self.errored.emit(str(ex))
            return
        self.done.emit(result)


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
        elif generator == "value_list":
            layout.addWidget(QLabel("Values (comma-separated):"))
            values_edit = QLineEdit(", ".join(str(v) for v in options.get("values", [])))
            layout.addWidget(values_edit)
            layout.addWidget(QLabel(
                "Weights (optional, comma-separated, same count as values):"
            ))
            weights_edit = QLineEdit(", ".join(str(w) for w in options.get("weights", [])))
            layout.addWidget(weights_edit)
            self._fields["values"] = values_edit
            self._fields["weights"] = weights_edit
        else:
            layout.addWidget(QLabel("This generator has no options."))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        if self._generator == "value_list":
            values = [v.strip() for v in self._fields["values"].text().split(",") if v.strip()]
            weights_text = [w.strip() for w in self._fields["weights"].text().split(",") if w.strip()]
            result = {"values": values}
            if len(weights_text) == len(values):
                try:
                    result["weights"] = [float(w) for w in weights_text]
                except ValueError:
                    pass  # malformed weight — fall back to uniform, don't block Options from closing
            return result
        result = {}
        for key, widget in self._fields.items():
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                result[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()
        return result


class MockDataDialog(QDialog):
    # Issue #217 — bridge signals owned by this QDialog (main-thread
    # affinity), exactly like ui/connection_panel.py's _QueryWorker usage
    # (worker.done.connect(lambda ...: self._q_done.emit(...))). A signal
    # connected straight to a plain closure/lambda has no QObject to infer
    # thread affinity from, so Qt can't reliably queue delivery back to the
    # main thread — connecting through a Signal *owned by this QObject* to
    # a real bound method is what makes the cross-thread delivery correct.
    _gen_result_ready = Signal(dict, int)
    _gen_error = Signal(str, int)

    def __init__(self, table_name: str, columns: list[dict],
                 primary_keys: list[str], foreign_keys: list[dict],
                 generated_columns: list[str], dialect: str = "mysql",
                 fk_sampler=None, dependency_chain: DependencyChain = None,
                 schema_fetcher=None, existing_row_count_fetcher=None,
                 pk_offset_fetcher=None, unique_columns: set = None,
                 enum_values: dict = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Generate Mock Data — {table_name}")
        # Issue #219 — resizable rather than a fixed-size form; the column
        # grid and preview/SQL panes below actually use the extra space
        # (see the QSplitter further down), instead of the grid staying
        # pinned at a fixed height regardless of how big the window gets.
        self.resize(900, 640)
        self.setMinimumSize(640, 480)

        self._table_name = table_name
        self._dialect = dialect
        self._fk_sampler = fk_sampler
        self._fk_pool_cache: dict[tuple, list] = {}
        self._last_sql = ""

        # Issue #215 — dependency-ordered multi-table generation. Only
        # offered when the caller found ancestors beyond the root table;
        # ancestor metadata is fetched lazily (only once the user actually
        # checks the box below) so opening this dialog for a table with no
        # ancestors — the common case — pays nothing extra over today.
        self._dependency_chain = dependency_chain
        self._schema_fetcher = schema_fetcher
        self._existing_row_count_fetcher = existing_row_count_fetcher
        self._pk_offset_fetcher = pk_offset_fetcher
        self._ancestor_tables: list[str] = (
            dependency_chain.tables[:-1] if dependency_chain and len(dependency_chain.tables) > 1 else []
        )
        self._ancestor_plans: dict[str, TablePlan] = {}
        self._ancestor_row_spins: dict[str, QSpinBox] = {}
        self._ancestor_reuse: dict[str, bool] = {}
        self._ancestor_panel_built = False
        self._generated_pk_columns: list[tuple] = []
        # Include state to restore for a column when its generator is
        # switched away from "omit" — set only while "omit" is active, so a
        # column the user had already excluded for its own reason (not
        # because of "omit") comes back excluded rather than forced on.
        self._pre_omit_include: dict[str, bool] = {}

        generated_set = set(generated_columns)
        self._columns = [c for c in columns if c["Field"] not in generated_set]
        self._fk_map = {fk["column"]: fk for fk in foreign_keys}
        lone_pk = primary_keys[0] if len(primary_keys) == 1 else None

        self._primary_keys = primary_keys
        self._unique_columns = set(unique_columns or ())
        self._enum_values = dict(enum_values or {})

        self._specs: dict[str, gen.ColumnSpec] = {}
        for col in self._columns:
            field_name = col["Field"]
            is_pk = field_name == lone_pk
            is_fk = field_name in self._fk_map
            allowed_values = self._enum_values.get(field_name)
            generator = gen.infer_generator(col, is_pk=is_pk, is_fk=is_fk, allowed_values=allowed_values)
            if generator == "value_list" and allowed_values:
                options = {"values": allowed_values}
            elif generator == "array":
                options = {"element_bucket": gen.array_element_bucket(col)}
            else:
                options = {}
            self._specs[field_name] = gen.ColumnSpec(
                generator=generator, include=generator != "omit", options=options,
            )

        layout = QVBoxLayout(self)

        # Issue #219 — config controls (row count/seed/locale, the
        # dependency-chain opt-in) live in one fixed-height pane at the top
        # of a vertical QSplitter; the column grid and preview/SQL tabs
        # below get the growable panes, weighted so extra window height
        # goes where it's actually useful (same QSplitter approach
        # ui/schema_compare_dialog.py already uses).
        splitter = QSplitter(Qt.Vertical)
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Rows to generate:"))
        self._row_count_spin = QSpinBox()
        self._row_count_spin.setRange(1, _MAX_ROW_COUNT)
        self._row_count_spin.setValue(10)
        top_row.addWidget(self._row_count_spin)

        # Issue #216 — optional seed; blank means unseeded (today's
        # behavior, unchanged). Issue #218 — locale, defaulting to
        # English (US), the historical hardcoded behavior.
        top_row.addWidget(QLabel("Seed (optional):"))
        self._seed_edit = QLineEdit()
        self._seed_edit.setValidator(QIntValidator(0, 2_147_483_647, self))
        self._seed_edit.setFixedWidth(90)
        self._seed_edit.setPlaceholderText("random")
        top_row.addWidget(self._seed_edit)

        top_row.addWidget(QLabel("Locale:"))
        self._locale_combo = QComboBox()
        for display_name, code in gen.SUPPORTED_LOCALES:
            self._locale_combo.addItem(display_name, code)
        top_row.addWidget(self._locale_combo)

        top_row.addStretch()
        config_layout.addLayout(top_row)

        if self._ancestor_tables:
            self._include_deps_check = QCheckBox(
                f"Include dependent tables ({len(self._ancestor_tables)} table"
                f"{'s' if len(self._ancestor_tables) != 1 else ''} this one depends on)"
            )
            self._include_deps_check.toggled.connect(self._toggle_include_dependents)
            config_layout.addWidget(self._include_deps_check)

            self._ancestor_panel = QTableWidget(0, 3)
            self._ancestor_panel.setHorizontalHeaderLabels(["Table", "Status", "Rows"])
            self._ancestor_panel.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self._ancestor_panel.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self._ancestor_panel.setMaximumHeight(150)
            self._ancestor_panel.setVisible(False)
            config_layout.addWidget(self._ancestor_panel)
        else:
            self._include_deps_check = None
            self._ancestor_panel = None

        splitter.addWidget(config_widget)
        splitter.addWidget(self._build_column_grid())

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        self._tabs = QTabWidget()
        self._preview_table = QTableWidget()
        self._preview_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._tabs.addTab(self._preview_table, "Preview")
        self._sql_view = QTextEdit()
        self._sql_view.setReadOnly(True)
        self._sql_view.setStyleSheet(_PREVIEW_STYLE)
        self._tabs.addTab(self._sql_view, "SQL")
        preview_layout.addWidget(self._tabs)
        splitter.addWidget(preview_widget)

        # config pane stays put; the column grid and preview/SQL tabs share
        # extra window height, weighted toward preview since that's where
        # the actual generated data/SQL is judged.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        layout.addWidget(splitter, stretch=1)

        self._warning_label = QLabel("")
        self._warning_label.setWordWrap(True)
        self._warning_label.setStyleSheet("color: #c9622a;")
        layout.addWidget(self._warning_label)

        # Issue #217 — indeterminate while a background generation run is in
        # flight; hidden otherwise. Real progress isn't reported since the
        # actual bottleneck (Faker calls per row) isn't a fixed-cost
        # per-row loop worth instrumenting — just proof the app is alive.
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        action_row = QHBoxLayout()
        self._regen_btn = QPushButton("Regenerate")
        self._regen_btn.clicked.connect(self._regenerate)
        action_row.addWidget(self._regen_btn)
        copy_btn = QPushButton("Copy SQL")
        copy_btn.clicked.connect(self._copy_sql)
        action_row.addWidget(copy_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._ok_button = buttons.button(QDialogButtonBox.Ok)
        self._ok_button.setText("Insert into Database…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._run_token = 0
        self._gen_thread = None
        self._gen_worker = None
        self._pending_on_done = None
        self._gen_result_ready.connect(self._on_gen_result_ready)
        self._gen_error.connect(self._on_gen_error)
        self._regenerate()

    # ─── Column config grid ────────────────────────────────────────────────

    def _build_column_grid(self) -> QTableWidget:
        table = QTableWidget(len(self._columns), 6)
        table.setHorizontalHeaderLabels(["Column", "Type", "Include", "Generator", "Null %", "Options"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # Issue #219 — no hard height cap: this pane lives in the
        # constructor's QSplitter and grows with the window instead of
        # scrolling inside a fixed 240px box regardless of column count.

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

    def _sample_external(self, ref_table: str, ref_column: str) -> list:
        """Cached live sample of *ref_table.ref_column* — shared by the
        single-table path (`_fk_pool`) and, for issue #215, by chain mode's
        `generate_chain_dataframes(external_pool_fn=...)` for any ancestor
        edge that isn't resolved by an in-memory generated pool (a reused
        ancestor, a self-reference, or a broken-cycle fallback). One cache
        keyed by (ref_table, ref_column) means a table sampled for the
        root's own FK column and again as a chain ancestor only ever hits
        the database once."""
        if not self._fk_sampler:
            return []
        key = (ref_table, ref_column)
        if key not in self._fk_pool_cache:
            try:
                self._fk_pool_cache[key] = self._fk_sampler(ref_table, ref_column)
            except Exception:
                self._fk_pool_cache[key] = []
        return self._fk_pool_cache[key]

    def _fk_pool(self, name: str) -> list:
        fk = self._fk_map.get(name)
        if not fk:
            return []
        return self._sample_external(fk["ref_table"], fk["ref_column"])

    # ─── Multi-table (dependency chain) generation (issue #215) ────────────

    def _toggle_include_dependents(self, checked: bool):
        if checked and not self._ancestor_panel_built:
            self._build_ancestor_panel()
        self._ancestor_panel.setVisible(checked)
        self._regenerate()

    def _build_ancestor_panel(self):
        """Fetch each ancestor table's schema + existing row count once,
        lazily — only reached when the user actually checks "Include
        dependent tables". A table with existing rows defaults to
        "reuse" (row_count=0, sampled live like today's single-table FK
        pool); an empty one defaults to generating the same row count as
        the root table, editable per the user's confirmed preference."""
        self._ancestor_panel_built = True
        root_row_count = self._row_count_spin.value()
        self._ancestor_panel.setRowCount(len(self._ancestor_tables))

        for row, table in enumerate(self._ancestor_tables):
            self._ancestor_panel.setItem(row, 0, QTableWidgetItem(table))
            try:
                (columns, primary_keys, foreign_keys, generated_columns,
                 unique_columns, enum_values) = self._schema_fetcher(table)
                existing = self._existing_row_count_fetcher(table) if self._existing_row_count_fetcher else 0
            except Exception:
                columns, primary_keys, foreign_keys, generated_columns = [], [], [], []
                unique_columns, enum_values, existing = set(), {}, 0

            reuse = existing > 0
            status = f"{existing:,} existing rows — will reuse" if reuse else "empty — will generate"
            self._ancestor_panel.setItem(row, 1, QTableWidgetItem(status))

            if reuse:
                self._ancestor_panel.setItem(row, 2, QTableWidgetItem("—"))
                self._ancestor_plans[table] = TablePlan(
                    table=table, columns=columns, primary_keys=primary_keys,
                    foreign_keys=foreign_keys, generated_columns=generated_columns,
                    row_count=0, unique_columns=unique_columns, enum_values=enum_values,
                )
            else:
                spin = QSpinBox()
                spin.setRange(0, _MAX_ROW_COUNT)
                spin.setValue(root_row_count)
                spin.valueChanged.connect(lambda _v, t=table: self._on_ancestor_row_count_changed(t))
                self._ancestor_panel.setCellWidget(row, 2, spin)
                self._ancestor_row_spins[table] = spin
                self._ancestor_plans[table] = TablePlan(
                    table=table, columns=columns, primary_keys=primary_keys,
                    foreign_keys=foreign_keys, generated_columns=generated_columns,
                    row_count=root_row_count, unique_columns=unique_columns, enum_values=enum_values,
                    pk_offset=self._pk_offset_for(table, primary_keys),
                )

    def _pk_offset_for(self, table: str, primary_keys: list) -> int | None:
        if len(primary_keys) != 1 or not self._pk_offset_fetcher:
            return None
        try:
            return self._pk_offset_fetcher(table, primary_keys[0])
        except Exception:
            return None

    def _on_ancestor_row_count_changed(self, table: str):
        plan = self._ancestor_plans.get(table)
        if plan is not None:
            plan.row_count = self._ancestor_row_spins[table].value()
        self._regenerate()

    def generated_pk_columns(self) -> list:
        """[(table, pk_column), ...] for every table this dialog generated
        fresh rows for (chain mode only) with a single-column PK. The
        caller bumps each one's DB sequence after insert (issue #215;
        Postgres-only, a safe no-op elsewhere) so a later auto-assigned
        insert can't collide with the explicit values just written."""
        return self._generated_pk_columns

    def _regenerate(self):
        # Issues #216/#218 — re-applied on every Regenerate (not just once
        # at dialog construction) so a fixed seed reproduces byte-identical
        # SQL across repeated clicks, and changing the locale mid-session
        # takes effect immediately.
        seed_text = self._seed_edit.text().strip()
        gen.configure(locale=self._locale_combo.currentData(),
                      seed=int(seed_text) if seed_text else None)
        if self._include_deps_check is not None and self._include_deps_check.isChecked():
            self._regenerate_chain()
        else:
            self._regenerate_single()

    def _set_busy(self, busy: bool):
        self._progress_bar.setVisible(busy)
        self._regen_btn.setEnabled(not busy)
        self._ok_button.setEnabled(not busy)

    def _start_generation(self, fn, on_done, total_rows: int = 0):
        """Run *fn* (one of the pure _build_*_result functions above) and
        deliver its result dict to *on_done*. Below _BACKGROUND_ROW_THRESHOLD
        this just calls *fn* inline — a sub-second call, so QThread
        setup/teardown would be pure overhead, and every existing caller
        (including tests) keeps seeing Regenerate complete synchronously.
        At/above the threshold (issue #217's actual concern — a large row
        count risking a frozen window) it runs on a background QThread
        instead, with a monotonic run token to discard a stale result if
        the user changes settings and clicks Regenerate again before the
        previous run finished."""
        self._run_token += 1
        token = self._run_token
        if total_rows < _BACKGROUND_ROW_THRESHOLD:
            on_done(fn())
            return
        self._set_busy(True)
        self._pending_on_done = on_done

        thread = QThread(self)
        worker = _GenerationWorker(fn)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Bridge to this QDialog's own signals (see class docstring comment
        # above _gen_result_ready) — cheap re-emit from whichever thread
        # calls it, safe to call from the worker thread.
        worker.done.connect(lambda result: self._gen_result_ready.emit(result, token))
        worker.errored.connect(lambda message: self._gen_error.emit(message, token))
        # Same quit/deleteLater convention as _QueryWorker's usage in
        # ui/connection_panel.py.
        worker.done.connect(thread.quit)
        worker.errored.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        # Keep references alive for the run's duration (same as _QueryWorker
        # in ui/connection_panel.py) — nothing else holds them otherwise.
        self._gen_thread = thread
        self._gen_worker = worker
        thread.start()

    def _on_gen_result_ready(self, result: dict, token: int):
        self._gen_thread = None
        self._gen_worker = None
        on_done, self._pending_on_done = self._pending_on_done, None
        if token == self._run_token:
            self._set_busy(False)
            if on_done:
                on_done(result)

    def _on_gen_error(self, message: str, token: int):
        self._gen_thread = None
        self._gen_worker = None
        self._pending_on_done = None
        if token == self._run_token:
            self._set_busy(False)
            QMessageBox.critical(self, "Error", f"Mock data generation failed:\n{message}")

    def _regenerate_single(self):
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

        # fk_pools is already plain data (a cached, already-fetched sample) —
        # the only thing handed to the background thread below, alongside a
        # deep copy of self._specs so a mid-run edit to the column grid
        # can't race the worker reading the same ColumnSpec objects.
        specs_snapshot = copy.deepcopy(self._specs)
        columns, table_name, dialect, unique_columns = (
            self._columns, self._table_name, self._dialect, self._unique_columns,
        )

        def _done(result):
            self._populate_preview(result["df"])
            self._last_sql = result["sql"]
            self._sql_view.setPlainText(self._last_sql or "-- No columns selected to insert.")
            self._generated_pk_columns = []

        self._start_generation(
            lambda: _build_single_result(columns, row_count, specs_snapshot, fk_pools,
                                          unique_columns, table_name, dialect),
            _done, total_rows=row_count,
        )

    def _regenerate_chain(self):
        chain = self._dependency_chain
        root_row_count = self._row_count_spin.value()
        plans = dict(self._ancestor_plans)
        plans[self._table_name] = TablePlan(
            table=self._table_name, columns=self._columns,
            primary_keys=self._primary_keys, foreign_keys=list(self._fk_map.values()),
            generated_columns=[], row_count=root_row_count,
            unique_columns=self._unique_columns, enum_values=self._enum_values,
        )

        # Issue #217: pre-resolve every external FK pool this chain could
        # need *before* threading, so the background worker never touches
        # self.db_service (not thread-safe to share with the main thread's
        # own use of that same connection). Mirrors exactly which edges
        # generate_chain_dataframes would otherwise call external_pool_fn
        # for: a declared external edge, or a parent table that's being
        # reused (row_count <= 0) rather than freshly generated.
        for table in chain.tables:
            plan = plans.get(table)
            if not plan or plan.row_count <= 0:
                continue
            ext_cols = chain.external_edges.get(table, set())
            for fk in plan.foreign_keys:
                column, ref_table, ref_column = fk.get("column"), fk.get("ref_table"), fk.get("ref_column")
                if not ref_table or not ref_column:
                    continue
                ref_plan = plans.get(ref_table)
                if column in ext_cols or not ref_plan or ref_plan.row_count <= 0:
                    self._sample_external(ref_table, ref_column)
        pool_cache_snapshot = dict(self._fk_pool_cache)
        plans_snapshot = copy.deepcopy(plans)
        dialect = self._dialect

        def _done(result):
            dataframes = result["dataframes"]
            root_df = dataframes.get(self._table_name)
            if root_df is not None:
                self._populate_preview(root_df)
                extra = [t for t in chain.tables if t != self._table_name and dataframes.get(t) is not None]
                if extra:
                    current = self._tabs.tabText(0)
                    self._tabs.setTabText(
                        0, f"{current}  (+{len(extra)} ancestor table{'s' if len(extra) != 1 else ''})"
                    )

            self._last_sql = result["sql"]
            self._sql_view.setPlainText(self._last_sql or "-- No columns selected to insert.")
            self._generated_pk_columns = result["generated_pk_columns"]

            notes = []
            if chain.truncated:
                notes.append("dependency chain was too large — only part of it is included")
            if any(chain.external_edges.values()):
                notes.append(
                    "some references use existing data instead of freshly generated "
                    "rows (self-referencing or circular foreign keys)"
                )
            for table, plan in plans.items():
                df = dataframes.get(table)
                if df is None or plan.row_count <= 0:
                    continue
                not_null_cols = {c["Field"] for c in plan.columns if c.get("Null") == "NO"}
                for fk in plan.foreign_keys:
                    col = fk["column"]
                    if col in df.columns and col in not_null_cols and df[col].isna().any():
                        notes.append(
                            f"'{table}.{col}' references an empty table — the insert will fail (NOT NULL)"
                        )
            self._warning_label.setText("⚠ " + "; ".join(notes) if notes else "")

        total_rows = sum(p.row_count for p in plans.values() if p.row_count > 0)
        self._start_generation(
            lambda: _build_chain_result(chain, plans_snapshot,
                                         lambda t, c: pool_cache_snapshot.get((t, c), []), dialect),
            _done, total_rows=total_rows,
        )

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
