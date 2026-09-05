"""Impact Analysis UI (issue #236) — the Impact Analysis report dialog and
the shared warning-text builder reused by the pre-DROP confirmation
prompts in ui/connection_panel.py (_delete_table, show_alter_table_editor).
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTabWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QPlainTextEdit, QLineEdit,
    QDialogButtonBox, QHeaderView,
)

from services.dependency_analyzer import CATEGORIES, DependencyReport

# (singular, plural) noun for each kind's summary bullet — foreign keys
# read as "references" (matching the reference design); the rest just use
# their plain plural, e.g. "0 views", "1 trigger".
_SUMMARY_LABELS = {
    "foreign_key": ("foreign-key reference", "foreign-key references"),
    "view": ("view", "views"),
    "function": ("function", "functions"),
    "procedure": ("procedure", "procedures"),
    "trigger": ("trigger", "triggers"),
}

_BULLET_LABELS = {
    "foreign_key": "Foreign key",
    "view": "View",
    "function": "Function",
    "procedure": "Procedure",
    "trigger": "Trigger",
}


def _pluralize(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _summary_line(report: DependencyReport) -> str:
    """"6 foreign-key references • 0 views • 0 functions • 0 triggers • 0
    procedures" — every category, in fixed order, counts included even
    when zero (the dialog's tab bar carries the same zero-count
    categories, just disabled — this line is a one-glance total)."""
    parts = []
    for attr, kind, _title in CATEGORIES:
        count = len(report.by_attr(attr))
        parts.append(f"{count} {_pluralize(count, *_SUMMARY_LABELS[kind])}")
    return " • ".join(parts)


def impact_warning_text(report: DependencyReport) -> str:
    """Plain-text `⚠ N thing(s) depend on X and may break:` block for
    embedding into an existing confirmation dialog (before DROP TABLE /
    DROP COLUMN). "" when the report is empty or unsupported, so callers
    can skip the section entirely rather than show an empty header. Unlike
    the dialog's summary line, this only lists categories that actually
    have results — it's a warning, not a status readout."""
    if not report or not report.supported or report.is_empty():
        return ""
    lines = [f"⚠ {report.total()} thing(s) depend on '{report.target_label}' and may break:"]
    for attr, kind, _title in CATEGORIES:
        label = _BULLET_LABELS[kind]
        for ref in report.by_attr(attr):
            detail = ref.detail if kind == "foreign_key" else ref.name
            lines.append(f"  • {label}: {detail}")
    return "\n".join(lines)


def _new_table(headers: list) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.verticalHeader().setVisible(False)
    # Interactive (not ResizeToContents) so the user can drag column
    # borders to resize — _build_tab() below sizes them sensibly at
    # populate time via resizeColumnsToContents(), it just isn't locked
    # there.
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    return table


def _show_definition_popup(parent, title: str, text: str):
    """Read-only popup for a view/function/procedure/trigger's real
    definition — already fetched into DependencyRef.detail when the report
    was built, so this needs no extra database round-trip."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(600, 400)
    layout = QVBoxLayout(dlg)
    box = QPlainTextEdit(text or "(definition not available)")
    box.setReadOnly(True)
    layout.addWidget(box)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    dlg.exec_()


def _pill_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        "padding: 3px 10px; border-radius: 9px; font-size: 11px; "
        "background-color: rgba(127, 127, 127, 0.16); "
        "border: 1px solid rgba(127, 127, 127, 0.32);"
    )
    return label


def _circle_icon(emoji: str) -> QLabel:
    label = QLabel(emoji)
    label.setFixedSize(56, 56)
    label.setAlignment(Qt.AlignCenter)
    label.setStyleSheet(
        "font-size: 24px; border-radius: 28px; "
        "background-color: rgba(90, 140, 255, 0.16);"
    )
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def _info_card(title: str, body: str) -> QFrame:
    card = QFrame()
    card.setStyleSheet(
        "QFrame { background-color: rgba(90, 140, 255, 0.08); "
        "border: 1px solid rgba(90, 140, 255, 0.25); border-radius: 6px; }"
    )
    card_layout = QVBoxLayout(card)
    header = QHBoxLayout()
    header.addWidget(QLabel("ℹ️"))
    header.addWidget(QLabel(f"<b>{title}</b>"))
    header.addStretch()
    card_layout.addLayout(header)
    body_label = QLabel(body)
    body_label.setWordWrap(True)
    card_layout.addWidget(body_label)
    return card


class ImpactAnalysisDialog(QDialog):
    """Read-only report of everything that depends on a table or column —
    incoming foreign keys, and views/functions/procedures/triggers whose
    definition mentions it — one tab per category, disabled when that
    category has no results. Foreign-key and view rows navigate to the
    referencing table/view (open_table, mirrors ui/erd_dialog.py's
    ErdDialog.open_table, wired the same way in ui/connection_panel.py);
    function/procedure/trigger rows show their real definition instead,
    since there's no table to open for those."""

    open_table = Signal(str)

    def __init__(self, report: DependencyReport, parent=None):
        super().__init__(parent)
        self._report = report
        self.setWindowTitle(f"Impact Analysis — {report.target_label}")
        self.resize(640, 460)

        layout = QVBoxLayout(self)

        if report.supported and report.is_empty():
            self._build_empty_state(layout, report)
            return

        layout.addWidget(QLabel(f"<b>{report.target_label}</b>"))

        if not report.supported:
            note = QLabel(
                "Impact analysis isn't supported for this connection's "
                "database type — foreign-key, view, function, procedure, "
                "and trigger lookups require MySQL or PostgreSQL catalog "
                "metadata."
            )
            note.setWordWrap(True)
            layout.addWidget(note)
        else:
            summary = QLabel(_summary_line(report))
            summary.setStyleSheet("color: gray;")
            summary.setWordWrap(True)
            layout.addWidget(summary)

            tabs = QTabWidget()
            for attr, kind, title in CATEGORIES:
                refs = report.by_attr(attr)
                index = tabs.addTab(self._build_tab(kind, refs), f"{title} ({len(refs)})")
                tabs.setTabEnabled(index, bool(refs))
            layout.addWidget(tabs)

            hint = QLabel("Double-click a row, or use its Go / View Definition button.")
            hint.setStyleSheet("color: gray; font-size: 11px;")
            layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_empty_state(self, layout: QVBoxLayout, report: DependencyReport):
        """No known dependents — a reassuring "confirmed safe" screen
        rather than a bare one-liner, with a short explainer for anyone
        who lands here without already knowing what Impact Analysis is."""
        noun = "column" if report.column else "table"

        header = QHBoxLayout()
        header.addWidget(_circle_icon("🔗"), 0, Qt.AlignTop)
        text_col = QVBoxLayout()
        title = QLabel(f"<span style='font-size:15px'><b>{report.target_label}</b></span>")
        text_col.addWidget(title)
        text_col.addWidget(_pill_label("No dependencies found"), 0, Qt.AlignLeft)
        header.addLayout(text_col)
        header.addStretch()
        layout.addLayout(header)

        message = QLabel(
            f"This {noun} is not referenced by any known foreign keys, "
            "views, functions, procedures, or triggers."
        )
        message.setWordWrap(True)
        message.setStyleSheet("color: gray;")
        layout.addWidget(message)

        layout.addWidget(_divider())
        layout.addWidget(_info_card(
            "What is Impact Analysis?",
            f"Impact Analysis shows all database objects that depend on this "
            f"{noun}. This helps you understand what might be affected if "
            "you change or drop it.",
        ))
        layout.addStretch()

        footer = QHBoxLayout()
        footer.addWidget(QLabel("💡"))
        tip = QLabel("Tip: Try Impact Analysis on other tables or columns.")
        tip.setStyleSheet("color: gray; font-size: 11px;")
        footer.addWidget(tip)
        footer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def _build_tab(self, kind: str, refs: list) -> QTableWidget:
        if kind == "foreign_key":
            table = _new_table(["Referencing Table", "Column", "References", ""])
            table.setRowCount(len(refs))
            for row, ref in enumerate(refs):
                table.setItem(row, 0, QTableWidgetItem(ref.name))
                table.setItem(row, 1, QTableWidgetItem(ref.column))
                table.setItem(row, 2, QTableWidgetItem(f"{self._report.table}.{ref.ref_column}"))
                table.setCellWidget(row, 3, self._go_button(lambda _c=False, n=ref.name: self.open_table.emit(n)))
            table.itemDoubleClicked.connect(lambda item: self.open_table.emit(refs[item.row()].name))
            table.resizeColumnsToContents()
            return table

        if kind == "view":
            table = _new_table(["Name", ""])
            table.setRowCount(len(refs))
            for row, ref in enumerate(refs):
                table.setItem(row, 0, QTableWidgetItem(ref.name))
                table.setCellWidget(row, 1, self._go_button(
                    lambda _c=False, n=ref.name: self.open_table.emit(n), label="Open"))
            table.itemDoubleClicked.connect(lambda item: self.open_table.emit(refs[item.row()].name))
            table.resizeColumnsToContents()
            return table

        # function / procedure / trigger — no table to open, show the
        # real definition text instead.
        table = _new_table(["Name", ""])
        table.setRowCount(len(refs))
        for row, ref in enumerate(refs):
            table.setItem(row, 0, QTableWidgetItem(ref.name))
            table.setCellWidget(row, 1, self._go_button(
                lambda _c=False, r=ref: self._show_definition(r), label="View Definition"))
        table.itemDoubleClicked.connect(lambda item: self._show_definition(refs[item.row()]))
        table.resizeColumnsToContents()
        return table

    @staticmethod
    def _go_button(handler, label: str = "Go") -> QPushButton:
        btn = QPushButton(label)
        btn.clicked.connect(handler)
        return btn

    def _show_definition(self, ref):
        _show_definition_popup(self, f"Definition — {ref.name}", ref.detail)


class DatabaseImpactDialog(QDialog):
    """Database-wide Impact Analysis (issue #236's "Database" option) —
    one row per table, each with a Details button that opens the exact
    same per-table ImpactAnalysisDialog using the report already computed
    here (services/dependency_analyzer.find_database_dependents() — no
    extra query on drill-down). Foreign-key navigation from a drilled-into
    report is re-emitted up through this dialog's own open_table signal,
    same convention as ImpactAnalysisDialog's own."""

    open_table = Signal(str)

    def __init__(self, reports: list, parent=None):
        super().__init__(parent)
        self._by_table = {r.table: r for r in reports}
        self.setWindowTitle("Impact Analysis — Database")
        self.resize(680, 480)

        layout = QVBoxLayout(self)

        if not reports:
            layout.addWidget(QLabel(
                "No tables found, or impact analysis isn't supported for "
                "this connection's database type."
            ))
            buttons = QDialogButtonBox(QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)
            return

        layout.addWidget(QLabel(f"<b>Database Impact Overview</b> — {len(reports)} tables"))

        search = QLineEdit()
        search.setPlaceholderText("🔍 Filter by table name...")
        search.setClearButtonEnabled(True)
        search.textChanged.connect(self._apply_filter)
        layout.addWidget(search)

        headers = ["Table"] + [title for _attr, _kind, title in CATEGORIES] + ["Total", ""]
        table = _new_table(headers)
        table.setSortingEnabled(False)
        ordered = sorted(reports, key=lambda r: r.total(), reverse=True)
        table.setRowCount(len(ordered))
        for row, report in enumerate(ordered):
            table.setItem(row, 0, QTableWidgetItem(report.table))
            for col, (attr, _kind, _title) in enumerate(CATEGORIES, start=1):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, len(report.by_attr(attr)))
                table.setItem(row, col, item)
            total_item = QTableWidgetItem()
            total_item.setData(Qt.DisplayRole, report.total())
            table.setItem(row, len(CATEGORIES) + 1, total_item)
            table.setCellWidget(row, len(CATEGORIES) + 2, self._details_button(report))
        table.resizeColumnsToContents()
        table.setSortingEnabled(True)   # header-click sort, safe post-populate: cell handlers close over the report object, not a row index
        table.itemDoubleClicked.connect(self._on_row_double_clicked)
        self._table = table
        layout.addWidget(table)

        hint = QLabel("Double-click a row, or use its Details button. Click a column header to sort.")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for row in range(self._table.rowCount()):
            name = self._table.item(row, 0).text().lower()
            self._table.setRowHidden(row, bool(needle) and needle not in name)

    def _details_button(self, report: DependencyReport) -> QPushButton:
        btn = QPushButton("Details")
        btn.clicked.connect(lambda _c=False, r=report: self._show_details(r))
        return btn

    def _on_row_double_clicked(self, item):
        name = self._table.item(item.row(), 0).text()
        report = self._by_table.get(name)
        if report:
            self._show_details(report)

    def _show_details(self, report: DependencyReport):
        dlg = ImpactAnalysisDialog(report, parent=self)
        dlg.open_table.connect(self.open_table.emit)
        dlg.exec_()
