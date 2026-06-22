"""
QueryVerifierDialog
────────────────────
A dialog that lets the user paste an original query and an optimised
query side-by-side, run a full verification in the background, and
inspect the diff results in a clear, structured report.

Checks reported:
  • Row count match
  • Column set match
  • Symmetric diff (rows present in one result but not the other)
  • Aggregate (SUM / MIN / MAX) comparison per numeric / date column
"""

from __future__ import annotations

import re
import threading

from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QKeySequence, QTextCharFormat, QColor, QTextCursor, QBrush, QShortcut
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QPushButton,
    QSplitter, QScrollArea, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QSizePolicy, QLineEdit,
    QFormLayout, QGroupBox, QCheckBox, QSpinBox, QMenu, QApplication,
    QAbstractItemView, QPlainTextEdit,
)

from ui.code_editor import CodeEditor
from ui.sql_highlighter import SqlHighlighter
from services.query_verifier import QueryVerifier, VerifyResult


# ─── Background worker ────────────────────────────────────────────────────────

# Regex: matches ${varName}, :varName (not inside quotes), {{varName}}
_PARAM_RE = re.compile(
    r"\$\{(\w+)\}"          # ${varName}
    r"|(?<![:\w]):(\w+)"     # :varName  (not ::cast)
    r"|\{\{(\w+)\}\}"        # {{varName}}
)


def _extract_params(sql: str) -> list[str]:
    """Return unique parameter names found in sql, preserving order."""
    seen: list[str] = []
    for m in _PARAM_RE.finditer(sql):
        name = m.group(1) or m.group(2) or m.group(3)
        if name and name not in seen:
            seen.append(name)
    return seen


def _substitute_params(sql: str, values: dict[str, str]) -> str:
    """Replace all parameter placeholders with their literal values."""
    def replacer(m):
        name = m.group(1) or m.group(2) or m.group(3)
        return values.get(name, m.group(0))   # leave untouched if not provided
    return _PARAM_RE.sub(replacer, sql)


class _VerifyWorker(QObject):
    done    = Signal(object)   # VerifyResult
    errored = Signal(str)

    def __init__(self, db_service, original_query: str, optimised_query: str,
                 row_limit: int = 0):
        super().__init__()
        self._db        = db_service
        self._orig      = original_query
        self._opt       = optimised_query
        self._row_limit = row_limit

    def run(self):
        from services.db_service import DbService
        dedicated = DbService()
        try:
            # Use a fresh dedicated connection so we never touch the shared
            # connection that the main thread may be using simultaneously.
            # This also avoids the (0, '') "connection closed" pymysql error.
            dedicated.connect(self._db._config)
            verifier = QueryVerifier(dedicated)
            result   = verifier.verify(self._orig, self._opt,
                                       row_limit=self._row_limit)
            self.done.emit(result)
        except Exception as ex:
            self.errored.emit(str(ex))
        finally:
            try:
                dedicated.disconnect()
            except Exception:
                pass


# ─── Dialog ───────────────────────────────────────────────────────────────────

_PASS_COLOR = "#30d158"   # macOS green
_FAIL_COLOR = "#ff453a"   # macOS red
_WARN_COLOR = "#ff9f0a"   # macOS orange
_MUTED      = "#8e8e93"
_BORDER     = "#3a3a3c"
_PANEL_BG   = "#2c2c2e"
_BG         = "#1c1c1e"
_TEXT       = "#e5e5ea"


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color:{_MUTED}; font-size:11px; font-weight:600;"
        f" text-transform:uppercase; letter-spacing:0.5px;"
        f" padding:6px 0 2px 0;"
    )
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color:{_BORDER}; background:{_BORDER}; max-height:1px;")
    return line


def _check_row(icon: str, icon_color: str, message: str, sub: str = "") -> QWidget:
    """One row of the results report: icon + main message + optional subtext."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 2, 0, 2)
    h.setSpacing(8)

    icon_lbl = QLabel(icon)
    icon_lbl.setFixedWidth(20)
    icon_lbl.setAlignment(Qt.AlignCenter)
    icon_lbl.setStyleSheet(f"color:{icon_color}; font-size:14px;")
    h.addWidget(icon_lbl)

    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 0, 0)
    body.setSpacing(1)

    main_lbl = QLabel(message)
    main_lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
    main_lbl.setWordWrap(True)
    main_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    body.addWidget(main_lbl)

    if sub:
        sub_lbl = QLabel(sub)
        sub_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
        sub_lbl.setWordWrap(True)
        sub_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        body.addWidget(sub_lbl)

    h.addLayout(body, 1)
    return w


class QueryVerifierDialog(QDialog):

    def __init__(self, db_service, initial_query: str = "", parent=None):
        super().__init__(parent)
        self._db          = db_service
        self._thread      = None
        self._worker      = None
        self._cancel_flag = threading.Event()

        self.setWindowTitle("⚖  Query Verifier")
        self.setMinimumSize(1000, 720)
        self.resize(1100, 800)
        self.setModal(False)

        self._build_ui(initial_query)
        self._apply_styles()

    # ─── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self, initial_query: str):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Editor row ─────────────────────────────────────────────────────
        editor_splitter = QSplitter(Qt.Horizontal)
        editor_splitter.setHandleWidth(6)

        for side, label_text, query_text in [
            ("orig",  "Original Query",   ""),
            ("opt",   "Optimised Query",  initial_query),
        ]:
            box = QWidget()
            bv  = QVBoxLayout(box)
            bv.setContentsMargins(0, 0, 0, 0)
            bv.setSpacing(4)

            hdr_row = QHBoxLayout()
            hdr_row.setContentsMargins(0, 0, 0, 0)

            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color:{_TEXT}; font-size:12px; font-weight:600;"
                f" padding:2px 0;"
            )
            hdr_row.addWidget(lbl, 1)
            bv.addLayout(hdr_row)

            # ── Find bar (hidden by default) ───────────────────────────────
            find_bar = QWidget()
            find_bar.setVisible(False)
            fb_row = QHBoxLayout(find_bar)
            fb_row.setContentsMargins(0, 2, 0, 2)
            fb_row.setSpacing(4)

            find_input = QLineEdit()
            find_input.setPlaceholderText("Search…")
            find_input.setFixedHeight(26)
            find_input.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" border-radius:4px; padding:0 6px; font-size:12px;"
            )
            fb_row.addWidget(find_input, 1)

            match_lbl = QLabel("")
            match_lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
            match_lbl.setFixedWidth(60)
            fb_row.addWidget(match_lbl)

            prev_btn = QPushButton("▲")
            next_btn = QPushButton("▼")
            close_btn = QPushButton("✕")
            for b in (prev_btn, next_btn, close_btn):
                b.setFixedSize(22, 22)
                b.setStyleSheet(
                    f"background:transparent; color:{_MUTED}; border:none;"
                    f" font-size:11px;"
                )
            fb_row.addWidget(prev_btn)
            fb_row.addWidget(next_btn)
            fb_row.addWidget(close_btn)
            bv.addWidget(find_bar)

            editor = CodeEditor()
            editor.setPlaceholderText(f"Paste {label_text.lower()} here…")
            editor.setPlainText(query_text)
            editor.setMinimumHeight(160)
            _SqlHighlighter = SqlHighlighter   # local alias
            _SqlHighlighter(editor.document())
            bv.addWidget(editor, 1)

            if side == "orig":
                self._orig_editor = editor
                self._orig_find_bar = find_bar
                self._orig_find_input = find_input
                self._orig_match_lbl = match_lbl
            else:
                self._opt_editor = editor
                self._opt_find_bar = find_bar
                self._opt_find_input = find_input
                self._opt_match_lbl = match_lbl

            # Wire find bar ────────────────────────────────────────────────
            _editor = editor
            _bar    = find_bar
            _inp    = find_input
            _mlbl   = match_lbl

            def _show_find(ed=_editor, bar=_bar, inp=_inp):
                bar.setVisible(True)
                inp.setFocus()
                inp.selectAll()

            def _hide_find(ed=_editor, bar=_bar, inp=_inp):
                bar.setVisible(False)
                ed.setFocus()
                # Clear highlights
                cur = ed.textCursor()
                cur.select(QTextCursor.Document)
                fmt = QTextCharFormat()
                cur.setCharFormat(fmt)
                cur.clearSelection()
                ed.setTextCursor(cur)

            def _do_find(text, forward=True, ed=_editor, mlbl=_mlbl):
                if not text:
                    mlbl.setText("")
                    return
                doc = ed.document()
                flags = QTextCursor.FindCaseSensitively if any(c.isupper() for c in text) else QTextCursor.FindFlags()
                if not forward:
                    flags |= QTextCursor.FindBackward
                # Highlight all
                fmt_hi = QTextCharFormat()
                fmt_hi.setBackground(QColor("#ff9f0a"))
                fmt_hi.setForeground(QColor("#000"))
                fmt_clr = QTextCharFormat()
                cur_all = QTextCursor(doc)
                cur_all.select(QTextCursor.Document)
                cur_all.setCharFormat(fmt_clr)
                count = 0
                c = QTextCursor(doc)
                while True:
                    c = doc.find(text, c, flags & ~QTextCursor.FindBackward)
                    if c.isNull():
                        break
                    c.mergeCharFormat(fmt_hi)
                    count += 1
                mlbl.setText(f"{count} found" if count else "not found")
                mlbl.setStyleSheet(f"color:{'#ff453a' if count == 0 else _MUTED}; font-size:11px;")
                # Move to next/prev match from current cursor
                found = ed.find(text, flags)
                if not found:
                    # wrap around
                    c2 = ed.textCursor()
                    c2.movePosition(QTextCursor.Start if forward else QTextCursor.End)
                    ed.setTextCursor(c2)
                    ed.find(text, flags)

            find_input.textChanged.connect(lambda t, fn=_do_find: fn(t))
            next_btn.clicked.connect(lambda _, inp=_inp, fn=_do_find: fn(inp.text(), True))
            prev_btn.clicked.connect(lambda _, inp=_inp, fn=_do_find: fn(inp.text(), False))
            find_input.returnPressed.connect(lambda inp=_inp, fn=_do_find: fn(inp.text(), True))
            close_btn.clicked.connect(lambda _, hf=_hide_find: hf())

            # Cmd+F shortcut scoped to this editor's parent box
            sc = QShortcut(QKeySequence("Ctrl+F"), box)
            sc.activated.connect(lambda sf=_show_find: sf())
            # ESC closes find bar
            sc_esc = QShortcut(QKeySequence("Escape"), box)
            sc_esc.activated.connect(lambda hf=_hide_find, bar=_bar: hf() if bar.isVisible() else None)

            editor_splitter.addWidget(box)

        editor_splitter.setSizes([500, 500])
        root.addWidget(editor_splitter, 2)

        # ── Parameters section (auto-shown when vars detected) ─────────────
        self._param_box = QGroupBox("Query Parameters")
        self._param_box.setVisible(False)
        self._param_box.setStyleSheet(
            f"QGroupBox {{ color:{_MUTED}; font-size:11px; font-weight:600;"
            f" border:1px solid {_BORDER}; border-radius:6px; margin-top:6px;"
            f" padding:6px 8px; }}"
            f" QGroupBox::title {{ subcontrol-origin:margin; left:8px;"
            f" padding:0 4px; }}"
        )
        self._param_form = QFormLayout(self._param_box)
        self._param_form.setContentsMargins(8, 12, 8, 6)
        self._param_form.setSpacing(6)
        self._param_inputs: dict[str, QLineEdit] = {}
        root.addWidget(self._param_box)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 4)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._status_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._status_lbl.setCursor(Qt.IBeamCursor)
        btn_row.addWidget(self._status_lbl, 1)

        # Limit checkbox + spinbox
        self._limit_chk = QCheckBox("Limit")
        self._limit_chk.setStyleSheet(f"color:{_MUTED}; font-size:12px;")
        self._limit_chk.setToolTip("Wrap each query in SELECT * FROM (...) LIMIT N before comparing")
        btn_row.addWidget(self._limit_chk)

        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(100, 1_000_000)
        self._limit_spin.setValue(1000)
        self._limit_spin.setSingleStep(1000)
        self._limit_spin.setFixedHeight(28)
        self._limit_spin.setFixedWidth(90)
        self._limit_spin.setStyleSheet(
            f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
            f" border-radius:4px; padding:0 4px; font-size:12px;"
        )
        self._limit_spin.setEnabled(False)
        self._limit_chk.toggled.connect(self._limit_spin.setEnabled)
        btn_row.addWidget(self._limit_spin)

        self._run_btn = QPushButton("▶  Run Verification")
        self._run_btn.setFixedHeight(32)
        self._run_btn.setMinimumWidth(180)
        self._run_btn.clicked.connect(self._start_verification)
        btn_row.addWidget(self._run_btn)

        root.addLayout(btn_row)

        # ── Results scroll area ────────────────────────────────────────────
        root.addWidget(_divider())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        self._results_widget = QWidget()
        self._results_layout = QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(0, 4, 0, 4)
        self._results_layout.setSpacing(4)
        self._results_layout.addStretch()

        scroll.setWidget(self._results_widget)
        root.addWidget(scroll, 3)

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QDialog {{
                background: {_BG};
                color: {_TEXT};
            }}
            QWidget {{
                background: {_BG};
                color: {_TEXT};
                font-size: 13px;
            }}
            QSplitter::handle {{
                background: {_BORDER};
            }}
            QScrollArea {{
                background: {_BG};
                border: none;
            }}
            QPushButton#runBtn {{
                background: #0A84FF;
                color: #fff;
                border: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 13px;
                padding: 0 20px;
            }}
            QPushButton#runBtn:hover  {{ background: #228BFF; }}
            QPushButton#runBtn:pressed {{ background: #0066CC; }}
            QPushButton#runBtn:disabled {{
                background: #2c2c2e;
                color: {_MUTED};
                border: 1px solid {_BORDER};
            }}
            QTableWidget {{
                background: {_PANEL_BG};
                color: {_TEXT};
                border: 1px solid {_BORDER};
                border-radius: 6px;
                gridline-color: {_BORDER};
                font-size: 12px;
            }}
            QHeaderView::section {{
                background: #3a3a3c;
                color: {_MUTED};
                font-size: 11px;
                font-weight: 600;
                padding: 4px 8px;
                border: none;
                border-right: 1px solid {_BORDER};
            }}
            QTableWidget::item {{
                padding: 4px 8px;
            }}
        """)
        self._run_btn.setObjectName("runBtn")
        self._run_btn.setStyleSheet("""
            QPushButton {
                background: #0A84FF;
                color: #fff;
                border: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 13px;
                padding: 0 20px;
            }
            QPushButton:hover  { background: #228BFF; }
            QPushButton:pressed { background: #0066CC; }
            QPushButton:disabled {
                background: #2c2c2e;
                color: #8e8e93;
                border: 1px solid #3a3a3c;
            }
        """)
        # Refresh params when editors change (debounced)
        self._param_timer = QTimer(self)
        self._param_timer.setSingleShot(True)
        self._param_timer.setInterval(400)
        self._param_timer.timeout.connect(self._refresh_params)
        self._orig_editor.textChanged.connect(self._param_timer.start)
        self._opt_editor.textChanged.connect(self._param_timer.start)
        # Trigger once for initial content
        QTimer.singleShot(0, self._refresh_params)

    # ─── Parameter helpers ──────────────────────────────────────────────────

    def _refresh_params(self):
        """Scan both editors for parameter placeholders and rebuild the form."""
        combined = (self._orig_editor.toPlainText() + "\n" +
                    self._opt_editor.toPlainText())
        params = _extract_params(combined)

        # Remove inputs no longer present, add new ones, keep existing values
        existing = dict(self._param_inputs)
        # Clear form
        while self._param_form.rowCount():
            self._param_form.removeRow(0)
        self._param_inputs.clear()

        for name in params:
            lbl = QLabel(name)
            lbl.setStyleSheet(f"color:{_TEXT}; font-size:12px;")
            inp = QLineEdit()
            inp.setPlaceholderText(f"value for {name}")
            inp.setFixedHeight(26)
            inp.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" border-radius:4px; padding:0 6px; font-size:12px;"
            )
            # Restore previous value if the param already existed
            if name in existing:
                inp.setText(existing[name].text())
            self._param_form.addRow(lbl, inp)
            self._param_inputs[name] = inp

        self._param_box.setVisible(bool(params))

    def _resolve_query(self, sql: str) -> str:
        """Substitute parameter values into sql; raise if any are blank."""
        values = {name: inp.text() for name, inp in self._param_inputs.items()}
        missing = [k for k, v in values.items() if not v.strip()]
        if missing:
            raise ValueError(f"Missing values for: {', '.join(missing)}")
        return _substitute_params(sql, values)

    # ─── Verification logic ───────────────────────────────────────────────

    def _start_verification(self):
        orig_raw = self._orig_editor.toPlainText().strip()
        opt_raw  = self._opt_editor.toPlainText().strip()

        if not orig_raw:
            self._status_lbl.setText("⚠  Please enter the original query.")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return
        if not opt_raw:
            self._status_lbl.setText("⚠  Please enter the optimised query.")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return

        # Substitute parameters
        try:
            orig = self._resolve_query(orig_raw)
            opt  = self._resolve_query(opt_raw)
        except ValueError as ve:
            self._status_lbl.setText(f"⚠  {ve}")
            self._status_lbl.setStyleSheet(f"color:{_WARN_COLOR}; font-size:12px;")
            return

        self._clear_results()
        self._run_btn.setEnabled(False)
        self._run_btn.setText("⏳  Running…")
        self._status_lbl.setText("Running both queries in the background…")
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")

        row_limit = self._limit_spin.value() if self._limit_chk.isChecked() else 0
        worker = _VerifyWorker(self._db, orig, opt, row_limit=row_limit)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_verify_done)
        worker.errored.connect(self._on_verify_error)
        worker.done.connect(thread.quit)
        worker.errored.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_verify_done(self, result: VerifyResult):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Verification")
        self._render_results(result)

    def _on_verify_error(self, message: str):
        self._run_btn.setEnabled(True)
        self._run_btn.setText("▶  Run Verification")
        self._status_lbl.setText(f"Error: {message}")
        self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")

    # ─── Results rendering ────────────────────────────────────────────────────

    def _clear_results(self):
        layout = self._results_layout
        while layout.count():
            item = layout.takeAt(0)
            w    = item.widget()
            if w:
                w.deleteLater()

    def _render_results(self, r: VerifyResult):
        self._clear_results()
        add = self._results_layout.addWidget

        if r.error:
            self._status_lbl.setText(f"Failed: {r.error}")
            self._status_lbl.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px;")

            # Error card with copyable text + Copy button
            err_card = QWidget()
            err_card.setStyleSheet(
                f"background:{_FAIL_COLOR}18; border:1px solid {_FAIL_COLOR}55;"
                f" border-radius:6px;"
            )
            ec_layout = QVBoxLayout(err_card)
            ec_layout.setContentsMargins(10, 8, 10, 8)
            ec_layout.setSpacing(6)

            title_row = QHBoxLayout()
            icon = QLabel("✕  Verification failed with an error")
            icon.setStyleSheet(f"color:{_FAIL_COLOR}; font-size:12px; font-weight:600;")
            title_row.addWidget(icon, 1)
            copy_btn = QPushButton("⍘ Copy")
            copy_btn.setFixedHeight(22)
            copy_btn.setStyleSheet(
                f"background:transparent; color:{_MUTED}; border:1px solid {_BORDER};"
                f" border-radius:3px; font-size:11px; padding:0 8px;"
            )
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(r.error))
            title_row.addWidget(copy_btn)
            ec_layout.addLayout(title_row)

            err_txt = QPlainTextEdit(r.error)
            err_txt.setReadOnly(True)
            err_txt.setStyleSheet(
                f"background:transparent; color:{_TEXT}; border:none;"
                f" font-size:12px; font-family:Menlo,Monaco,'Courier New',monospace;"
            )
            err_txt.setFixedHeight(min(22 * r.error.count('\n') + 60, 200))
            err_txt.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            ec_layout.addWidget(err_txt)

            add(err_card)
            self._results_layout.addStretch()
            return

        # ── Overall badge ──────────────────────────────────────────────────
        badge_text  = "✅  PASSED — queries return equivalent data" if r.passed else "❌  FAILED — queries return different data"
        badge_color = _PASS_COLOR if r.passed else _FAIL_COLOR
        badge = QLabel(badge_text)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background:{badge_color}22; color:{badge_color};"
            f" border:1px solid {badge_color}55;"
            f" border-radius:8px; font-size:14px; font-weight:700;"
            f" padding:10px 16px; margin-bottom:6px;"
        )
        add(badge)

        self._status_lbl.setText(
            f"Original: {r.elapsed_original:.2f}s   |   "
            f"Optimised: {r.elapsed_optimised:.2f}s   |   "
            f"Speed-up: {(r.elapsed_original / r.elapsed_optimised):.1f}×"
            if r.elapsed_optimised > 0 else ""
        )
        self._status_lbl.setStyleSheet(f"color:{_MUTED}; font-size:12px;")

        # ── Row counts ─────────────────────────────────────────────────────
        add(_section_label("Row Count"))
        add(_divider())

        if r.count_match:
            add(_check_row(
                "✅", _PASS_COLOR,
                f"Row count matches: {r.count_original:,} rows",
                f"Original: {r.count_original:,}   Optimised: {r.count_optimised:,}"
            ))
        else:
            add(_check_row(
                "❌", _FAIL_COLOR,
                f"Row count mismatch!",
                f"Original: {r.count_original:,}   Optimised: {r.count_optimised:,}"
                f"   Difference: {abs(r.count_original - r.count_optimised):,}"
            ))

        # ── Columns ────────────────────────────────────────────────────────
        add(_section_label("Columns"))
        add(_divider())

        if r.cols_match:
            add(_check_row(
                "✅", _PASS_COLOR,
                f"Column set matches: {len(r.cols_original)} columns",
                sub=", ".join(r.cols_original[:10]) + ("…" if len(r.cols_original) > 10 else "")
            ))
        else:
            add(_check_row(
                "❌", _FAIL_COLOR,
                "Column sets differ!",
            ))
            if r.cols_only_in_original:
                add(_check_row("→", _WARN_COLOR,
                               f"Only in original: {', '.join(r.cols_only_in_original)}"))
            if r.cols_only_in_optimised:
                add(_check_row("→", _WARN_COLOR,
                               f"Only in optimised: {', '.join(r.cols_only_in_optimised)}"))

        # ── Row diff ───────────────────────────────────────────────────────
        diff_cols = getattr(r, "_diff_cols", [])
        diff_label = "Row-Level Diff"
        if not r.cols_match and diff_cols:
            diff_label += f"  (on {len(diff_cols)} common columns)"
        add(_section_label(diff_label))
        add(_divider())

        if not diff_cols:
            add(_check_row("⚠", _WARN_COLOR, "No common columns — row diff skipped."))
        elif r.rows_only_in_original == 0 and r.rows_only_in_optimised == 0:
            if r.col_diff_rows:
                add(_check_row(
                    "❌", _FAIL_COLOR,
                    f"{len(r.col_diff_rows)} row(s) have column-level value differences.",
                    sub="Same rows present in both results, but some cell values differ."
                ))
                add(_section_label("Column-Level Diff (cell values differ)"))
                add(self._build_col_diff_table(r.col_diff_rows, diff_cols))
            else:
                if not r.cols_match:
                    add(_check_row("✅", _PASS_COLOR,
                                   "No differing rows on common columns.",
                                   sub=f"Compared on: {', '.join(diff_cols)}"))
                else:
                    add(_check_row("✅", _PASS_COLOR,
                                   "No differing rows — both results are identical."))
        else:
            if r.rows_only_in_original > 0:
                add(_check_row(
                    "❌", _FAIL_COLOR,
                    f"{r.rows_only_in_original:,} row(s) present in original but NOT in optimised.",
                ))
                if r.diff_sample_original is not None:
                    add(_section_label(f"Sample (up to 10) — rows only in original"))
                    add(self._build_df_table(r.diff_sample_original))

            if r.rows_only_in_optimised > 0:
                add(_check_row(
                    "❌", _FAIL_COLOR,
                    f"{r.rows_only_in_optimised:,} row(s) present in optimised but NOT in original.",
                ))
                if r.diff_sample_optimised is not None:
                    add(_section_label(f"Sample (up to 10) — rows only in optimised"))
                    add(self._build_df_table(r.diff_sample_optimised))

        # ── Aggregates ─────────────────────────────────────────────────────
        if r.agg_rows:
            add(_section_label("Aggregate Checks (numeric / date columns)"))
            add(_divider())

            agg_table = QTableWidget(len(r.agg_rows), 7)
            agg_table.setHorizontalHeaderLabels([
                "Column", "SUM (orig)", "SUM (opt)",
                "MIN (orig)", "MIN (opt)",
                "MAX (orig)", "MAX (opt)",
            ])
            agg_table.verticalHeader().setVisible(False)
            agg_table.horizontalHeader().setStretchLastSection(False)
            agg_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
            agg_table.setSelectionBehavior(QTableWidget.SelectRows)
            agg_table.setEditTriggers(QTableWidget.NoEditTriggers)
            agg_table.setFixedHeight(min(36 * len(r.agg_rows) + 40, 300))
            agg_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

            for row_idx, ar in enumerate(r.agg_rows):
                color = _PASS_COLOR if ar.match else _FAIL_COLOR
                icon  = "✅" if ar.match else "❌"
                values = [
                    f"{icon}  {ar.column}",
                    ar.sum_orig, ar.sum_opt,
                    ar.min_orig, ar.min_opt,
                    ar.max_orig, ar.max_opt,
                ]
                for col_idx, val in enumerate(values):
                    item = QTableWidgetItem(str(val))
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    if col_idx == 0:
                        item.setForeground(
                            __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(color)
                        )
                    # Highlight mismatched SUM cells
                    if not ar.match and col_idx in (1, 2):
                        item.setBackground(
                            __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(_FAIL_COLOR + "33")
                        )
                    agg_table.setItem(row_idx, col_idx, item)

            add(agg_table)

        # ── EXPLAIN plans ──────────────────────────────────────────────────
        if r.explain_original or r.explain_optimised:
            add(_section_label("EXPLAIN Plans"))
            add(_divider())

            explain_note = QLabel(
                "Type legend: "
                "<span style='color:#30d158;'>const/eq_ref/ref</span> = index lookup  "
                "<span style='color:#ff9f0a;'>range</span> = partial scan  "
                "<span style='color:#ff453a;'>ALL</span> = full table scan"
            )
            explain_note.setTextFormat(Qt.RichText)
            explain_note.setStyleSheet(f"color:{_MUTED}; font-size:11px; padding:2px 0 4px 0;")
            add(explain_note)

            explain_splitter = QSplitter(Qt.Horizontal)
            explain_splitter.setHandleWidth(4)
            explain_splitter.addWidget(
                self._build_explain_table(r.explain_original, "Original Query"))
            explain_splitter.addWidget(
                self._build_explain_table(r.explain_optimised, "Optimised Query"))
            add(explain_splitter)

        self._results_layout.addStretch()

    def _build_df_table(self, df, diff_cols_per_row: dict = None) -> QWidget:
        """Render a DataFrame as a styled table with copy button + context menu.

        diff_cols_per_row: {row_idx: set_of_col_names} — cells to highlight.
        """
        from PySide6.QtGui import QColor, QBrush

        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 2, 0, 4)
        cv.setSpacing(4)

        # ── Toolbar: copy buttons ──────────────────────────────────────────
        toolbar = QWidget()
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        tb.addStretch()

        copy_sel_btn = QPushButton("⎘ Copy Selected")
        copy_all_btn = QPushButton("⎘ Copy All as CSV")
        for b in (copy_sel_btn, copy_all_btn):
            b.setFixedHeight(22)
            b.setStyleSheet(
                f"background:{_PANEL_BG}; color:{_MUTED}; border:1px solid {_BORDER};"
                f" border-radius:3px; font-size:11px; padding:0 8px;"
            )
        tb.addWidget(copy_sel_btn)
        tb.addWidget(copy_all_btn)
        cv.addWidget(toolbar)

        # ── Table ──────────────────────────────────────────────────────────
        cols = list(df.columns)
        tbl  = QTableWidget(len(df), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(False)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tbl.setFixedHeight(min(36 * len(df) + 40, 320))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tbl.setContextMenuPolicy(Qt.CustomContextMenu)

        for r_i, (_, row) in enumerate(df.iterrows()):
            for c_i, col in enumerate(cols):
                val = str(row[col])
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                # Cell-level diff highlighting
                if diff_cols_per_row:
                    info = diff_cols_per_row.get(r_i)
                    if info and col in info:
                        side, _ = info[col]
                        bg = QColor(_FAIL_COLOR + "44") if side == "orig" else QColor(_PASS_COLOR + "44")
                        item.setBackground(QBrush(bg))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)

        # ── Copy helpers ──────────────────────────────────────────────────
        def _rows_to_csv(row_indices):
            lines = ["\t".join(cols)]
            for ri in sorted(row_indices):
                cells = [tbl.item(ri, ci).text() if tbl.item(ri, ci) else ""
                         for ci in range(len(cols))]
                lines.append("\t".join(cells))
            return "\n".join(lines)

        def _copy_selected():
            rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
            if rows:
                QApplication.clipboard().setText(_rows_to_csv(rows))

        def _copy_all():
            QApplication.clipboard().setText(_rows_to_csv(range(tbl.rowCount())))

        copy_sel_btn.clicked.connect(_copy_selected)
        copy_all_btn.clicked.connect(_copy_all)

        # Right-click context menu
        def _context_menu(pos):
            menu = QMenu(tbl)
            menu.setStyleSheet(
                f"QMenu {{ background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" font-size:12px; }}"
                f" QMenu::item:selected {{ background:#0A84FF; color:#fff; }}"
            )
            sel_rows = sorted({idx.row() for idx in tbl.selectedIndexes()})
            if sel_rows:
                act_copy_sel = menu.addAction(f"⎘  Copy {len(sel_rows)} selected row(s)")
                act_copy_sel.triggered.connect(_copy_selected)
            act_sel_all = menu.addAction("Select All")
            act_sel_all.triggered.connect(tbl.selectAll)
            act_copy_all = menu.addAction("⎘  Copy All as CSV")
            act_copy_all.triggered.connect(_copy_all)
            menu.exec(tbl.viewport().mapToGlobal(pos))

        tbl.customContextMenuRequested.connect(_context_menu)

        return container

    def _build_col_diff_table(self, col_diff_rows, common_cols) -> QWidget:
        """Side-by-side column diff: Row | Column | Original (red) | Optimised (green)."""
        from services.query_verifier import ColDiffRow
        from PySide6.QtGui import QColor, QBrush

        # Flatten: one row per (row_idx, col)
        flat_rows = []
        for dr in col_diff_rows:
            for col, (ov, pv) in dr.diffs.items():
                flat_rows.append((dr.row_idx, col, ov, pv))

        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 2, 0, 4)
        cv.setSpacing(4)

        # Toolbar
        toolbar = QWidget()
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(6)
        info_lbl = QLabel(
            f"<span style='color:{_MUTED}; font-size:11px;'>"
            f"Showing {len(flat_rows)} cell differences across {len(col_diff_rows)} rows"
            f" — cells highlighted: "
            f"<span style='color:{_FAIL_COLOR};'>■</span> original  "
            f"<span style='color:{_PASS_COLOR};'>■</span> optimised</span>"
        )
        info_lbl.setTextFormat(Qt.RichText)
        tb.addWidget(info_lbl, 1)
        copy_btn = QPushButton("⎘ Copy")
        copy_btn.setFixedHeight(22)
        copy_btn.setStyleSheet(
            f"background:{_PANEL_BG}; color:{_MUTED}; border:1px solid {_BORDER};"
            f" border-radius:3px; font-size:11px; padding:0 8px;"
        )
        tb.addWidget(copy_btn)
        cv.addWidget(toolbar)

        tbl = QTableWidget(len(flat_rows), 4)
        tbl.setHorizontalHeaderLabels(["Row #", "Column", "Original Value", "Optimised Value"])
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setSelectionMode(QAbstractItemView.ExtendedSelection)
        tbl.setFixedHeight(min(30 * len(flat_rows) + 40, 400))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tbl.setContextMenuPolicy(Qt.CustomContextMenu)

        for r_i, (row_no, col, ov, pv) in enumerate(flat_rows):
            for c_i, (val, bg) in enumerate([
                (str(row_no), None),
                (col, None),
                (ov, QColor(_FAIL_COLOR + "44")),
                (pv, QColor(_PASS_COLOR + "44")),
            ]):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if bg:
                    item.setBackground(QBrush(bg))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)

        def _copy_all_diff():
            lines = ["Row\tColumn\tOriginal\tOptimised"]
            for r_i, (row_no, col, ov, pv) in enumerate(flat_rows):
                lines.append(f"{row_no}\t{col}\t{ov}\t{pv}")
            QApplication.clipboard().setText("\n".join(lines))

        copy_btn.clicked.connect(_copy_all_diff)

        def _context_menu(pos):
            menu = QMenu(tbl)
            menu.setStyleSheet(
                f"QMenu {{ background:{_PANEL_BG}; color:{_TEXT}; border:1px solid {_BORDER};"
                f" font-size:12px; }}"
                f" QMenu::item:selected {{ background:#0A84FF; color:#fff; }}"
            )
            menu.addAction("⎘  Copy All Diffs").triggered.connect(_copy_all_diff)
            menu.addAction("Select All").triggered.connect(tbl.selectAll)
            menu.exec(tbl.viewport().mapToGlobal(pos))

        tbl.customContextMenuRequested.connect(_context_menu)
        return container

    def _build_explain_table(self, explain_rows, title: str) -> QWidget:
        """Render EXPLAIN plan rows as a compact table."""
        from services.query_verifier import ExplainRow

        # Access type quality ranking (best → worst)
        _ACCESS_RANK = {
            "system": 0, "const": 1, "eq_ref": 2, "ref": 3,
            "fulltext": 4, "ref_or_null": 5, "index_merge": 6,
            "unique_subquery": 7, "index_subquery": 8,
            "range": 9, "index": 10, "ALL": 11,
        }
        _ACCESS_COLOR = {
            **{k: _PASS_COLOR for k in ("system", "const", "eq_ref", "ref")},
            **{k: _WARN_COLOR for k in ("range", "index_merge", "ref_or_null")},
            **{k: _FAIL_COLOR for k in ("index", "ALL")},
        }

        container = QWidget()
        cv = QVBoxLayout(container)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(2)

        header = QLabel(title)
        header.setStyleSheet(f"color:{_TEXT}; font-size:12px; font-weight:600; padding:2px 0;")
        cv.addWidget(header)

        if not explain_rows:
            lbl = QLabel("EXPLAIN not available for this query type.")
            lbl.setStyleSheet(f"color:{_MUTED}; font-size:11px;")
            cv.addWidget(lbl)
            return container

        cols = ["#", "Table", "Type", "Key", "Key Len", "Rows", "Filtered %", "Extra"]
        tbl = QTableWidget(len(explain_rows), len(cols))
        tbl.setHorizontalHeaderLabels(cols)
        tbl.verticalHeader().setVisible(False)
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setEditTriggers(QTableWidget.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.setFixedHeight(min(32 * len(explain_rows) + 40, 260))
        tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        for r_i, er in enumerate(explain_rows):
            row_vals = [er.id, er.table, er.access_type, er.key,
                        er.key_len, er.rows, er.filtered, er.extra]
            for c_i, val in enumerate(row_vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                # Color-code the access type cell
                if c_i == 2 and val:  # Type column
                    color = _ACCESS_COLOR.get(val, _MUTED)
                    item.setForeground(QBrush(QColor(color)))
                # Highlight ALL (full table scan) in red background
                if c_i == 2 and val == "ALL":
                    item.setBackground(QBrush(QColor(_FAIL_COLOR + "33")))
                tbl.setItem(r_i, c_i, item)

        cv.addWidget(tbl)
        return container
