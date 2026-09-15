"""First-launch landing screen (see PRODUCT_STRATEGY.md's onboarding pass).

Shown at startup, before the connection picker, so a new user sees what
QForge does and can jump straight into a recent connection or a blank new
one. Doesn't touch connections.json or the OS keychain itself — it only
displays the already-sanitized `connections` list handed to it by
MainWindow (see main.py's _run_startup_flow, which reuses a hidden
ConnectionDialog for that, then does the actual connecting/prompting
based on this dialog's `action` / `selected_index` once it closes).

Four ways to leave this screen, read from `.action` after exec():
  - "connect" — a Recent Connections row was clicked; `.selected_index`
    is that connection's index in the `connections` list passed in.
  - "add_new" — "Add New Connection" or a quick-create pill was clicked;
    `.chosen_db_type` is set for a quick-create pill, else None.
  - "skip"    — "Skip for now".
  - "quit"    — the close (X) button, the native window close, or Escape.
    Closing this screen any of these ways quits the app outright (unlike
    "skip") since, with Recent Connections and Add New Connection both
    living here now, there's no bare "connection picker" left to fall
    back to that closing should reasonably land on instead.
"""
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QRadialGradient, QColor, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QCheckBox, QFrame,
)
from PySide6.QtSvgWidgets import QSvgWidget

from services import preferences
from utils import environment
from utils.paths import bundled_asset_path
from ui.theme_manager import ThemeManager

_PREF_KEY = "show_welcome_screen"

_MAX_RECENT_ROWS = 5

# (icon glyph, accent color, title, description) — decorative badge colors
# only, deliberately not reusing the environment-tier palette (ai/ui-design.md)
# since these carry no safety meaning.
_FEATURES = [
    ("❯_", "#4F8CFF", "Powerful SQL Editor", "Write, run and manage your queries."),
    ("☰", "#3DBD8A", "Explore Schema", "Browse tables, columns, indexes and relationships."),
    ("⊞", "#E0A23D", "Work with Data", "View, filter and edit your data easily."),
    ("✓", "#9B6BE0", "Built for Safety", "Read-only mode and safeguards for production."),
]

# QForge only actually supports these two (PRODUCT_STRATEGY.md: SQLite was
# supported at one point and has since been removed; Redis never was) —
# kept as one list so the promo text and the quick-create pills can't drift
# from each other or from reality.
_SUPPORTED_DBS = ["MySQL", "PostgreSQL"]

_TYPE_LABELS = {"mysql": "MySQL", "postgresql": "PostgreSQL"}


class WelcomeScreen(QDialog):
    """Uses ThemeManager's dark tokens directly rather than branching on the
    current theme: MainWindow always starts in dark mode (main.py sets
    current_theme = "dark" unconditionally, theme choice isn't persisted
    across restarts), and this screen only ever appears at startup."""

    def __init__(self, connections: list[dict], parent=None):
        super().__init__(parent)
        self._connections = connections
        self.action = None  # "connect" | "add_new" | "skip" | "quit"
        self.selected_index = None
        self.chosen_db_type = None

        self.setWindowTitle("Welcome to QForge")
        self.resize(1180, 720)
        self.setMinimumSize(1040, 680)
        self._build_ui()

    def paintEvent(self, event):
        """Soft blue aurora glows in the bottom-left/top-right corners,
        matching the source mockup's background — painted after the base
        QDialog background (from ThemeManager's app-wide stylesheet) and
        before any child widget, so it sits behind all real content."""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        radius = max(w, h) * 0.6

        bottom_left = QRadialGradient(QPointF(w * 0.05, h * 0.98), radius)
        bottom_left.setColorAt(0.0, QColor(37, 99, 235, 70))
        bottom_left.setColorAt(1.0, QColor(37, 99, 235, 0))
        painter.setBrush(QBrush(bottom_left))
        painter.drawEllipse(QPointF(w * 0.05, h * 0.98), radius, radius)

        top_right = QRadialGradient(QPointF(w * 0.97, h * 0.02), radius * 0.85)
        top_right.setColorAt(0.0, QColor(59, 130, 246, 55))
        top_right.setColorAt(1.0, QColor(59, 130, 246, 0))
        painter.setBrush(QBrush(top_right))
        painter.drawEllipse(QPointF(w * 0.97, h * 0.02), radius * 0.85, radius * 0.85)
        painter.end()

    def closeEvent(self, event):
        # Only the native window close / Escape reach here with .action
        # still unset — every button below sets .action before calling
        # accept()/reject(), so this is purely the "closed some other way"
        # catch-all, and it means quit (see module docstring).
        if self.action is None:
            self.action = "quit"
        super().closeEvent(event)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 20)
        outer.setSpacing(10)

        close_row = QHBoxLayout()
        close_row.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setProperty("flat", "true")
        close_btn.setStyleSheet(
            "QPushButton { border-radius: 14px; padding: 0; font-size: 13px; }"
        )
        close_btn.clicked.connect(self._on_quit)
        close_row.addWidget(close_btn)
        outer.addLayout(close_row)

        content_row = QHBoxLayout()
        content_row.setSpacing(28)
        content_row.addWidget(self._build_left_panel(), 5)
        content_row.addWidget(self._build_right_panel(), 6)
        outer.addLayout(content_row, 1)

    # ── Left panel: what QForge is ───────────────────────────────────────

    def _build_left_panel(self) -> QWidget:
        T = ThemeManager
        panel = QWidget()
        # A plain QWidget (unlike QFrame/QLabel) still picks up the app-wide
        # stylesheet's generic `QWidget { background: ... }` rule once
        # QApplication.setStyleSheet() is in play (main.py's apply_theme) —
        # left opaque, it'd paint over paintEvent()'s background glow.
        panel.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 12, 12, 12)
        layout.setSpacing(14)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        logo = QSvgWidget(bundled_asset_path("assets/logo_mark.svg"))
        logo.setFixedSize(48, 48)
        brand_row.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        name_lbl = QLabel("QForge")
        name_lbl.setStyleSheet(f"color: {T.D_TEXT}; font-size: 22px; font-weight: 700;")
        brand_text.addWidget(name_lbl)
        tagline_lbl = QLabel("Your database workspace")
        tagline_lbl.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 12px;")
        brand_text.addWidget(tagline_lbl)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        layout.addLayout(brand_row)

        headline = QLabel(
            f"<span style='font-size:32px; font-weight:800; color:{T.D_TEXT};'>"
            f"Connect.<br>Explore.<br></span>"
            f"<span style='font-size:32px; font-weight:800; color:{T.D_BLUE};'>Get things done.</span>"
        )
        headline.setTextFormat(Qt.RichText)
        layout.addWidget(headline)

        blurb = QLabel("A fast, focused database client for developers who work with data.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 13px;")
        layout.addWidget(blurb)

        features = QVBoxLayout()
        features.setSpacing(12)
        for icon, color, title_text, desc_text in _FEATURES:
            features.addLayout(self._build_feature_row(icon, color, title_text, desc_text))
        layout.addLayout(features)

        layout.addStretch()

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background: {T.D_BORDER}; max-height: 1px; border: none;")
        layout.addWidget(sep)

        supports_row = QHBoxLayout()
        supports_row.setSpacing(10)
        supports_lbl = QLabel("Supports")
        supports_lbl.setStyleSheet(f"color: {T.D_TEXT3}; font-size: 12px;")
        supports_row.addWidget(supports_lbl)
        for name in _SUPPORTED_DBS:
            db_lbl = QLabel(name)
            db_lbl.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 12px; font-weight: 600;")
            supports_row.addWidget(db_lbl)
        supports_row.addStretch()
        layout.addLayout(supports_row)

        self.show_on_launch_check = QCheckBox("Show this screen on launch")
        self.show_on_launch_check.setChecked(preferences.get(_PREF_KEY, True))
        self.show_on_launch_check.toggled.connect(lambda checked: preferences.set(_PREF_KEY, checked))
        layout.addWidget(self.show_on_launch_check)

        return panel

    def _build_feature_row(self, icon: str, color: str, title_text: str, desc_text: str) -> QHBoxLayout:
        T = ThemeManager
        row = QHBoxLayout()
        row.setSpacing(12)

        badge = QLabel(icon)
        badge.setFixedSize(36, 36)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            f"background: {T._alpha(color, '33')}; color: {color};"
            f" border-radius: 8px; font-size: 15px; font-weight: 700;"
        )
        row.addWidget(badge, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = QLabel(title_text)
        title_lbl.setStyleSheet(f"color: {T.D_TEXT}; font-size: 13.5px; font-weight: 700;")
        text_col.addWidget(title_lbl)
        desc_lbl = QLabel(desc_text)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 12px;")
        text_col.addWidget(desc_lbl)
        row.addLayout(text_col, 1)

        return row

    # ── Right panel: get connected ────────────────────────────────────────

    def _build_right_panel(self) -> QFrame:
        T = ThemeManager
        panel = QFrame()
        panel.setObjectName("connectPanel")
        panel.setStyleSheet(
            f"QFrame#connectPanel {{ background: {T._alpha(T.D_RAISED, 'cc')};"
            f" border: 1px solid {T.D_BORDER}; border-radius: 14px; }}"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        header_row = QHBoxLayout()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title_lbl = QLabel("Let's get you connected")
        title_lbl.setStyleSheet(f"color: {T.D_TEXT}; font-size: 19px; font-weight: 700;")
        header_text.addWidget(title_lbl)
        subtitle_lbl = QLabel("Add a new connection or open a recent one to get started.")
        subtitle_lbl.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 12.5px;")
        header_text.addWidget(subtitle_lbl)
        header_row.addLayout(header_text)
        header_row.addStretch()
        skip_btn = QPushButton("Skip for now")
        skip_btn.setProperty("flat", "true")
        skip_btn.setCursor(Qt.PointingHandCursor)
        skip_btn.clicked.connect(self._on_skip)
        header_row.addWidget(skip_btn, 0, Qt.AlignTop)
        layout.addLayout(header_row)

        layout.addWidget(self._build_add_new_button())

        layout.addWidget(self._build_recent_section(), 1)

        layout.addWidget(self._build_quick_create_row())
        layout.addWidget(self._build_tip_box())

        return panel

    def _build_add_new_button(self) -> QPushButton:
        T = ThemeManager
        btn = QPushButton()
        btn.setFixedHeight(58)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._on_add_new(None))

        inner = QHBoxLayout(btn)
        inner.setContentsMargins(18, 0, 18, 0)
        left_lbl = QLabel("+  Add New Connection  →")
        left_lbl.setStyleSheet("color: #ffffff; font-size: 14.5px; font-weight: 700; background: transparent;")
        inner.addWidget(left_lbl)
        inner.addStretch()
        right_lbl = QLabel("Set up a new database connection")
        right_lbl.setStyleSheet(f"color: {T._alpha('#ffffff', 'cc')}; font-size: 12px; background: transparent;")
        inner.addWidget(right_lbl)

        return btn

    def _build_recent_section(self) -> QWidget:
        T = ThemeManager
        section = QFrame()
        section.setObjectName("recentSection")
        section.setStyleSheet(
            f"QFrame#recentSection {{ background: {T.D_WORKSPACE}; border: 1px solid {T.D_BORDER}; border-radius: 10px; }}"
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header_lbl = QLabel("\U0001F551  Recent Connections")
        header_lbl.setStyleSheet(f"color: {T.D_TEXT}; font-size: 13px; font-weight: 700;")
        header.addWidget(header_lbl)
        header.addStretch()
        layout.addLayout(header)

        indexed = list(enumerate(self._connections))[:_MAX_RECENT_ROWS]
        if not indexed:
            empty_lbl = QLabel("No saved connections yet — add one above to get started.")
            empty_lbl.setWordWrap(True)
            empty_lbl.setStyleSheet(f"color: {T.D_TEXT3}; font-size: 12.5px; padding: 6px 0;")
            layout.addWidget(empty_lbl)
        else:
            for idx, conn in indexed:
                layout.addWidget(self._build_recent_row(idx, conn))
            remaining = len(self._connections) - len(indexed)
            if remaining > 0:
                more_lbl = QLabel(f"+{remaining} more in Add New Connection")
                more_lbl.setStyleSheet(f"color: {T.D_TEXT3}; font-size: 11.5px;")
                layout.addWidget(more_lbl)

        layout.addStretch()
        return section

    def _build_recent_row(self, idx: int, conn: dict) -> QFrame:
        T = ThemeManager
        row = QFrame()
        row.setObjectName("recentRow")
        row.setCursor(Qt.PointingHandCursor)
        row.setStyleSheet(
            f"QFrame#recentRow {{ background: transparent; border-radius: 6px; }}"
            f"QFrame#recentRow:hover {{ background: {T.D_HOVER}; }}"
        )
        row.mousePressEvent = lambda _event, i=idx: self._on_connect(i)

        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(10)

        db_type = conn.get("type", "mysql")
        avatar = QLabel(db_type[:1].upper())
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background: {T._alpha(T.D_BLUE, '33')}; color: {T.D_BLUE};"
            f" border-radius: 14px; font-size: 12px; font-weight: 700;"
        )
        h.addWidget(avatar)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        name_lbl = QLabel(conn.get("name", "Unnamed"))
        name_lbl.setStyleSheet(f"color: {T.D_TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        text_col.addWidget(name_lbl)
        detail_bits = [_TYPE_LABELS.get(db_type, db_type.upper())]
        if conn.get("host"):
            detail_bits.append(conn["host"])
        detail_lbl = QLabel("  ·  ".join(detail_bits))
        detail_lbl.setStyleSheet(f"color: {T.D_TEXT3}; font-size: 11.5px; background: transparent;")
        text_col.addWidget(detail_lbl)
        h.addLayout(text_col, 1)

        env = environment.normalize(conn.get("environment"))
        if env != "unclassified":
            bg, text_color, border = T.env_colors(env, is_dark=True)
            env_lbl = QLabel(environment.BADGE_LABELS.get(env, env.upper()))
            env_lbl.setStyleSheet(
                f"background: {bg}; color: {text_color}; border: 1px solid {border};"
                f" border-radius: 4px; padding: 2px 6px; font-size: 10px; font-weight: 700;"
            )
            h.addWidget(env_lbl)

        return row

    def _build_quick_create_row(self) -> QWidget:
        T = ThemeManager
        wrap = QWidget()
        wrap.setStyleSheet("background: transparent;")  # see _build_left_panel's comment
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        label = QLabel("Or create a connection to")
        label.setStyleSheet(f"color: {T.D_TEXT3}; font-size: 11.5px;")
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(label)
        pills = QHBoxLayout()
        pills.setSpacing(8)
        for name in _SUPPORTED_DBS:
            pill = QPushButton(name)
            pill.setProperty("flat", "true")
            pill.setCursor(Qt.PointingHandCursor)
            pill.clicked.connect(lambda _checked, n=name: self._on_add_new(n))
            pills.addWidget(pill)
        pills.addStretch()
        col.addLayout(pills)
        row.addLayout(col)
        return wrap

    def _build_tip_box(self) -> QFrame:
        T = ThemeManager
        box = QFrame()
        box.setObjectName("tipBox")
        box.setStyleSheet(
            f"QFrame#tipBox {{ background: {T._alpha('#E0A23D', '14')};"
            f" border: 1px solid {T._alpha('#E0A23D', '40')}; border-radius: 10px; }}"
        )
        h = QHBoxLayout(box)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(10)

        icon = QLabel("\U0001F4A1")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("background: transparent; font-size: 15px;")
        h.addWidget(icon, 0, Qt.AlignTop)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        tip_title = QLabel("Tip")
        tip_title.setStyleSheet(f"color: {T.D_TEXT}; font-size: 12.5px; font-weight: 700; background: transparent;")
        text_col.addWidget(tip_title)
        tip_body = QLabel("You can always add or manage connections later from the connection menu.")
        tip_body.setWordWrap(True)
        tip_body.setStyleSheet(f"color: {T.D_TEXT2}; font-size: 12px; background: transparent;")
        text_col.addWidget(tip_body)
        h.addLayout(text_col, 1)

        return box

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_connect(self, idx: int):
        self.action = "connect"
        self.selected_index = idx
        self.accept()

    def _on_add_new(self, db_type: str | None):
        self.action = "add_new"
        self.chosen_db_type = db_type
        self.accept()

    def _on_skip(self):
        self.action = "skip"
        self.accept()

    def _on_quit(self):
        self.action = "quit"
        self.reject()
