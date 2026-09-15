import json
import os
import uuid

from utils import credential_store
from utils import environment
from utils import onboarding
from utils.logger import get_logger
from utils.paths import app_data_dir
from ui.theme_manager import ThemeManager
from ui.upgrade_dialog import require_under_limit
from services import preferences
from services.entitlements import Limit

logger = get_logger()

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QWidget,
    QComboBox,
    QLabel,
    QCheckBox,
    QFileDialog,
    QMenu,
    QCompleter,
    QFrame,
    QGraphicsOpacityEffect,
)
from PySide6.QtGui import QShortcut, QKeySequence, QColor, QFont, QFontMetrics
from PySide6.QtCore import (
    Qt,
    QTimer,
    QEvent,
    QPropertyAnimation,
    QParallelAnimationGroup,
    QAbstractAnimation,
    QEasingCurve,
)


class ConnectionDialog(QDialog):

    _APP_DIR = str(app_data_dir())
    CONNECTION_FILE = os.path.join(_APP_DIR, "connections.json")
    LAST_CONNECTION_FILE = os.path.join(_APP_DIR, "last_connection.json")

    # Issue #116: bounds used to validate a hand-edited or malicious
    # connections.json on load rather than trusting its shape.
    _ALLOWED_TYPES = ("mysql", "postgresql")
    _MAX_STRING_LEN = 4096

    _TYPE_LABELS = {"mysql": "MySQL", "postgresql": "PostgreSQL"}
    _TYPE_ICONS = {"mysql": "\U0001F42C", "postgresql": "\U0001F418"}  # dolphin / elephant

    # Sidebar "Recent" quick filter — a small persisted MRU list of
    # connection ids, most-recent-first (services/preferences.py), separate
    # from LAST_CONNECTION_FILE (which only ever remembers the single most
    # recent one, for auto-selecting on next launch).
    _RECENT_PREF_KEY = "recent_connection_ids"
    _MAX_RECENT = 10

    # Form field widths (issue #55) — small/medium/large are fixed caps;
    # content-fit fields (host, database, ssh key path) start at the medium
    # floor and grow only as far as their actual text needs, up to _FIT_CAP,
    # instead of stretching to the dialog's full width.
    SMALL_FIELD_WIDTH = 110
    MEDIUM_FIELD_WIDTH = 220
    LARGE_FIELD_WIDTH = 320
    _FIT_CAP_WIDTH = 520

    def __init__(self, auto_connect_last=False, parent=None):
        super().__init__(parent)

        self.selected_connection = None
        self.auto_connect_last = auto_connect_last
        # (connection_id, "db"|"ssh") -> password, populated on demand so we
        # don't hit the OS keychain (and its access-control prompt) once per
        # saved profile every time the connection list loads or saves.
        self._resolved_passwords = {}
        # In-flight group expand/collapse fades, kept alive here since
        # nothing else holds a Python reference to them once started (see
        # _fade_in_group_children / _animate_group_collapse).
        self._active_row_animations = []

        self.setWindowTitle("Connection Manager")
        self._compact_height = 530
        self._expanded_height = 730
        self.resize(860, self._compact_height)
        self.setMinimumSize(800, 420)
        self.setSizeGripEnabled(True)

        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(self.save_connection)

        self.init_ui()
        self.load_connections()

        if auto_connect_last:
            self.select_last_connection()

    def init_ui(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── LEFT PANEL ──────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(310)
        left_panel.setObjectName("connectionsLeftPanel")
        # ID-scoped selector, not a bare "QWidget {...}" rule: the latter
        # cascades to every QWidget descendant (each row's icon/name/
        # subtitle labels included), giving each one its own right border
        # and showing up as stray vertical divider lines inside every row.
        left_panel.setStyleSheet("QWidget#connectionsLeftPanel { border-right: 1px solid #3a3a3c; }")
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(8)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.connection_search = QLineEdit()
        self.connection_search.setPlaceholderText("Search connections...")
        self.connection_search.setStyleSheet("font-size: 12px; padding: 6px 8px;")
        self.connection_search.textChanged.connect(lambda _text: self._apply_filters())
        self.connection_search.installEventFilter(self)
        search_row.addWidget(self.connection_search, 1)
        search_hint = QLabel("⌘F")
        search_hint.setStyleSheet(
            "color: #8b8b90; font-size: 10px; border: 1px solid #48484a;"
            " border-radius: 4px; padding: 2px 5px; background: #2c2c2e;"
        )
        search_row.addWidget(search_hint)
        left_layout.addLayout(search_row)
        search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        search_shortcut.activated.connect(self._focus_search)

        # Quick filters: All Connections / Recent / Favorites. Deliberately
        # no "Environments" smart-folder section and no per-row colored
        # dots — environment safety is still shown as TEXT in each row's
        # subtitle (see _build_connection_row_widget): colour alone must
        # never be the only thing communicating risk (ai/ui-design.md).
        self._quick_filter = "all"
        self._quick_filter_rows: dict[str, QFrame] = {}
        filters_box = QVBoxLayout()
        filters_box.setSpacing(2)
        for key, icon, label_text in (
            ("all", "▤", "All Connections"),
            ("recent", "\U0001F551", "Recent"),
            ("favorites", "★", "Favorites"),
        ):
            row = self._build_quick_filter_row(key, icon, label_text)
            self._quick_filter_rows[key] = row
            filters_box.addWidget(row)
        left_layout.addLayout(filters_box)

        self.connection_tree = QTreeWidget()
        self.connection_tree.setHeaderHidden(True)
        self.connection_tree.setIndentation(0)
        self.connection_tree.setRootIsDecorated(True)
        # Qt's native slide animation only works for delegate-painted rows;
        # every row here is a real setItemWidget widget, so its attempt at
        # animating the reveal just fights with our own opacity fade below
        # (_fade_in_group_children / _animate_group_collapse) — visible as
        # a jump on expand specifically, since collapse's fade finishes
        # *before* the row is actually removed and has nothing left to
        # clash with.
        self.connection_tree.setAnimated(False)
        # ── Drag-to-reorder ─────────────────────────────────────
        self.connection_tree.setDragEnabled(True)
        self.connection_tree.setAcceptDrops(True)
        self.connection_tree.setDropIndicatorShown(True)
        self.connection_tree.setDragDropMode(QTreeWidget.InternalMove)
        self.connection_tree.model().rowsMoved.connect(self._on_tree_rows_moved)
        # ── Right-click context menu ─────────────────────────────
        self.connection_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.connection_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        # setItemWidget rows suppress the delegate's own paint entirely
        # (including the QSS QTreeWidget::item:selected highlight), so the
        # "currently selected connection" affordance has to be applied to
        # the row widget by hand instead.
        self.connection_tree.currentItemChanged.connect(self._on_tree_current_item_changed)
        self.connection_tree.itemExpanded.connect(self._on_tree_group_expansion_changed)
        self.connection_tree.itemCollapsed.connect(self._on_tree_group_expansion_changed)
        left_layout.addWidget(self.connection_tree, 1)

        # Annotated empty state (issue #164) — a first-time user's only
        # in-app path to discovering SSH tunnels, the SQL editor, and the
        # differentiator features (Schema Compare, Query Verifier) beyond
        # the bare "add a connection" form on the right. Static copy, not
        # an interactive tour: those features live in the main window,
        # which doesn't exist yet at this point in a fresh install.
        self._first_run_hint = self._build_first_run_hint()
        left_layout.addWidget(self._first_run_hint)

        new_conn_btn = QPushButton("+ New Connection")
        new_conn_btn.setToolTip("Clear form to create a new connection")
        new_conn_btn.clicked.connect(self._new_connection)
        left_layout.addWidget(new_conn_btn)

        # Placeholder — no backing feature yet (GitHub issue #348); mirrors
        # ui/welcome_screen.py's identically-scoped button/message.
        import_btn = QPushButton("⤓  Import Connections")
        import_btn.setProperty("flat", "true")
        import_btn.setToolTip("Coming soon")
        import_btn.clicked.connect(self._show_import_not_available)
        left_layout.addWidget(import_btn)

        left_panel.setLayout(left_layout)
        layout.addWidget(left_panel)

        # ── RIGHT PANEL ─────────────────────────────────────────
        right_panel = QWidget()
        form_layout = QFormLayout()
        form_layout.setContentsMargins(20, 16, 20, 8)
        form_layout.setSpacing(8)
        form_layout.setHorizontalSpacing(12)
        # QFormLayout stretches every field to the row's full width by default;
        # cap short/medium fields so they don't waste space.
        SMALL_FIELD_WIDTH = self.SMALL_FIELD_WIDTH
        MEDIUM_FIELD_WIDTH = self.MEDIUM_FIELD_WIDTH
        LARGE_FIELD_WIDTH = self.LARGE_FIELD_WIDTH
        self.type_input = QComboBox()
        self.type_input.addItems(["MySQL", "PostgreSQL"])
        self.type_input.currentTextChanged.connect(self.on_type_changed)
        self.type_input.setMaximumWidth(SMALL_FIELD_WIDTH)

        self.name_input = QLineEdit()
        self.name_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        # Fixed enum, not free text — see utils/environment.py. Index 0 is
        # "Unclassified" so new connections default to it until the user
        # deliberately chooses a real tier.
        self.environment_input = QComboBox()
        self.environment_input.addItems(
            [environment.COMBO_LABELS[e] for e in environment.ENVIRONMENTS]
        )
        self.environment_input.currentIndexChanged.connect(self._on_environment_changed)
        self.environment_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.read_only_check = QCheckBox("Read-only (block writes in QForge)")
        # Editable combo that lists existing groups; typing a new name is allowed
        self.group_input = QComboBox()
        self.group_input.setEditable(True)
        self.group_input.setInsertPolicy(QComboBox.NoInsert)
        self.group_input.lineEdit().setPlaceholderText("e.g. Production, Staging, Local…")
        self.group_input.setMaximumWidth(LARGE_FIELD_WIDTH)
        # Issue #41: an editable QComboBox auto-installs a completer, but its
        # default mode only inline-completes the single closest match — no
        # popup of every matching group, and no substring matching (typing
        # "Prod" wouldn't surface "MFI Production"). Replace it with one in
        # popup mode using the combo's own model, so _populate_group_combo()
        # rebuilding that model (below) is the only place group names need
        # to be kept in sync.
        group_completer = QCompleter(self.group_input.model(), self)
        group_completer.setCaseSensitivity(Qt.CaseInsensitive)
        group_completer.setFilterMode(Qt.MatchContains)
        group_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.group_input.setCompleter(group_completer)
        self.host_input = QLineEdit()
        self.host_input.setMinimumWidth(MEDIUM_FIELD_WIDTH)
        self.host_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.host_input.textChanged.connect(lambda: self._fit_field_to_content(self.host_input))
        self.port_input = QLineEdit("3306")
        self.port_input.setMaximumWidth(SMALL_FIELD_WIDTH)
        # textEdited (not textChanged) fires only on real keystrokes/paste,
        # never on programmatic setText() — so this only flips true when the
        # user actually typed a port themselves, and on_type_changed below
        # can tell that apart from the field still holding its auto-filled
        # dialect default.
        self._port_edited_by_user = False
        self.port_input.textEdited.connect(self._on_port_edited_by_user)
        self.database_input = QLineEdit()
        self.database_input.setMinimumWidth(MEDIUM_FIELD_WIDTH)
        self.database_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.database_input.textChanged.connect(lambda: self._fit_field_to_content(self.database_input))
        self.user_input = QLineEdit()
        self.user_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)

        # Password with visibility toggle
        password_widget = QWidget()
        password_layout = QHBoxLayout(password_widget)
        password_layout.setContentsMargins(0, 0, 0, 0)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.password_visible_btn = QPushButton("👁")
        self.password_visible_btn.setMaximumWidth(30)
        self.password_visible_btn.setStyleSheet(
            "QPushButton { background: #3a3a3c; color: #e5e5ea; border: 1px solid #636366;"
            " border-radius: 4px; font-size: 13px; padding: 0; }"
            "QPushButton:hover { background: #48484a; }"
        )
        self.password_visible_btn.clicked.connect(self.toggle_password_visibility)
        password_layout.addWidget(self.password_input)
        password_layout.addWidget(self.password_visible_btn)
        # Without a trailing stretch, Qt's box layout misdistributes the
        # leftover space from password_input's capped max-width as a leading
        # gap instead of trailing space, floating the eye button away from
        # the field — reproduced in isolation, not specific to this widget.
        password_layout.addStretch()

        form_layout.addRow("Type", self.type_input)
        form_layout.addRow("Name", self.name_input)
        form_layout.addRow("Environment", self.environment_input)
        form_layout.addRow("", self.read_only_check)
        form_layout.addRow("Group", self.group_input)
        self.host_row = form_layout.addRow("Host", self.host_input)
        self.port_row = form_layout.addRow("Port", self.port_input)
        form_layout.addRow("Database/Path", self.database_input)
        self.user_row = form_layout.addRow("User", self.user_input)
        self.password_row = form_layout.addRow("Password", password_widget)

        # ── SSH Tunnel section ───────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #3a3a3c; margin: 4px 0;")
        form_layout.addRow(sep)

        self.ssh_enabled_check = QCheckBox("SSH Tunnel")
        self.ssh_enabled_check.toggled.connect(self.on_ssh_enabled_toggled)
        form_layout.addRow("", self.ssh_enabled_check)

        # Collapsible SSH details (hidden until checkbox is ticked)
        self.ssh_section = QWidget()
        ssh_inner = QFormLayout(self.ssh_section)
        ssh_inner.setContentsMargins(0, 4, 0, 0)
        ssh_inner.setSpacing(8)
        ssh_inner.setHorizontalSpacing(12)

        self.ssh_host_input = QLineEdit()
        self.ssh_host_input.setMinimumWidth(MEDIUM_FIELD_WIDTH)
        self.ssh_host_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.ssh_host_input.textChanged.connect(lambda: self._fit_field_to_content(self.ssh_host_input))
        self.ssh_port_input = QLineEdit("22")
        self.ssh_port_input.setMaximumWidth(SMALL_FIELD_WIDTH)
        self.ssh_user_input = QLineEdit()
        self.ssh_user_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)

        ssh_password_widget = QWidget()
        ssh_password_layout = QHBoxLayout(ssh_password_widget)
        ssh_password_layout.setContentsMargins(0, 0, 0, 0)
        self.ssh_password_input = QLineEdit()
        self.ssh_password_input.setEchoMode(QLineEdit.Password)
        self.ssh_password_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.ssh_password_visible_btn = QPushButton("👁")
        self.ssh_password_visible_btn.setMaximumWidth(30)
        self.ssh_password_visible_btn.setStyleSheet(
            "QPushButton { background: #3a3a3c; color: #e5e5ea; border: 1px solid #636366;"
            " border-radius: 4px; font-size: 13px; padding: 0; }"
            "QPushButton:hover { background: #48484a; }"
        )
        self.ssh_password_visible_btn.clicked.connect(self.toggle_ssh_password_visibility)
        ssh_password_layout.addWidget(self.ssh_password_input)
        ssh_password_layout.addWidget(self.ssh_password_visible_btn)
        ssh_password_layout.addStretch()

        self.ssh_use_key_checkbox = QCheckBox("Use SSH Key")
        self.ssh_use_key_checkbox.stateChanged.connect(self.on_ssh_key_checkbox_changed)

        self.ssh_key_path_input = QLineEdit()
        self.ssh_key_path_input.setPlaceholderText("Path to private key file (e.g., ~/.ssh/id_rsa)")
        self.ssh_key_path_input.setEnabled(False)
        self.ssh_key_path_input.setMinimumWidth(MEDIUM_FIELD_WIDTH)
        self.ssh_key_path_input.setMaximumWidth(MEDIUM_FIELD_WIDTH)
        self.ssh_key_path_input.textChanged.connect(lambda: self._fit_field_to_content(self.ssh_key_path_input))

        self.ssh_key_browse_btn = QPushButton("📁")
        self.ssh_key_browse_btn.setFixedWidth(40)
        self.ssh_key_browse_btn.setEnabled(False)
        self.ssh_key_browse_btn.clicked.connect(self.browse_ssh_key)

        ssh_key_layout = QHBoxLayout()
        ssh_key_layout.addWidget(self.ssh_key_path_input)
        ssh_key_layout.addWidget(self.ssh_key_browse_btn)
        ssh_key_layout.addStretch()
        ssh_key_layout.setContentsMargins(0, 0, 0, 0)
        ssh_key_widget = QWidget()
        ssh_key_widget.setLayout(ssh_key_layout)

        ssh_inner.addRow("SSH Host", self.ssh_host_input)
        ssh_inner.addRow("SSH Port", self.ssh_port_input)
        ssh_inner.addRow("SSH User", self.ssh_user_input)
        ssh_inner.addRow("SSH Password", ssh_password_widget)
        ssh_inner.addRow("", self.ssh_use_key_checkbox)
        ssh_inner.addRow("SSH Key Path", ssh_key_widget)

        self.ssh_section.setVisible(False)
        form_layout.addRow(self.ssh_section)

        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        self.test_status_label.setStyleSheet("padding: 5px; border-radius: 3px;")
        form_layout.addRow("", self.test_status_label)

        # Buttons: Save, Delete, Test, Connect
        btn_layout = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.delete_btn = QPushButton("Delete")
        self.test_btn = QPushButton("🔧 Test")
        self.connect_btn = QPushButton("✓ Connect")

        # Buttons in a QDialog default to autoDefault=True, which makes
        # whichever one has focus grow a native default-button bezel under
        # the QSS rounded-rect styling — its square corner pokes out past
        # the rounded corner. None of these should carry that chrome.
        for b in (self.save_btn, self.delete_btn, self.test_btn, self.connect_btn):
            b.setAutoDefault(False)

        btn_layout.addWidget(self.save_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.test_btn)
        btn_layout.addWidget(self.connect_btn)

        wrapper = QVBoxLayout()
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)
        wrapper.addLayout(form_layout)
        wrapper.addStretch()

        btn_bar = QWidget()
        btn_bar.setStyleSheet("QWidget { border-top: 1px solid #3a3a3c; }")
        btn_bar_layout = QHBoxLayout(btn_bar)
        btn_bar_layout.setContentsMargins(12, 8, 12, 10)
        btn_bar_layout.addLayout(btn_layout)
        wrapper.addWidget(btn_bar)

        right_panel.setLayout(wrapper)
        layout.addWidget(right_panel)

        self.setLayout(layout)

        # Wire events
        self.save_btn.clicked.connect(self.save_connection)
        self.delete_btn.clicked.connect(self.delete_connection)
        self.test_btn.clicked.connect(self.test_connection)
        self.connect_btn.clicked.connect(self.connect_selected)
        self.connection_tree.itemClicked.connect(self.on_tree_item_clicked)
        self.connection_tree.itemDoubleClicked.connect(
            lambda item, col: self.connect_selected() if item.parent() is not None else None
        )

    # ── Tree helpers ─────────────────────────────────────────────

    def _new_connection(self):
        """Deselect tree and clear form to create a new connection."""
        self.connection_tree.clearSelection()
        self.clear_form()
        self.name_input.setFocus()

    def _on_tree_rows_moved(self):
        """Rebuild self.connections order to match the new drag-dropped tree order."""
        new_order = []
        for g_idx in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(g_idx)
            for c_idx in range(group_item.childCount()):
                child = group_item.child(c_idx)
                conn_idx = child.data(0, Qt.UserRole)
                if conn_idx is not None and 0 <= conn_idx < len(self.connections):
                    new_order.append(self.connections[conn_idx])
        if len(new_order) == len(self.connections):
            self.connections = new_order
            self.save_connections()
            self.load_connections()  # re-index UserRole data

    def _on_tree_context_menu(self, pos):
        """Show context menu on right-click over a connection item."""
        item = self.connection_tree.itemAt(pos)
        if item is None or item.parent() is None:
            return  # clicked on group header or empty area
        menu = QMenu(self)
        dup_action = menu.addAction("Duplicate Connection")
        action = menu.exec(self.connection_tree.viewport().mapToGlobal(pos))
        if action == dup_action:
            conn_idx = item.data(0, Qt.UserRole)
            if conn_idx is not None:
                import copy
                new_conn = copy.deepcopy(self.connections[conn_idx])
                new_conn["name"] = new_conn["name"] + " Copy"

                # A deepcopy still carries the original's "id" — since
                # credentials are keyed by id in the keychain, that meant
                # the duplicate and the original silently shared one
                # keychain entry: editing/saving the duplicate's password
                # overwrote the original's (issue #36). Give the duplicate
                # its own id, then copy the actual credentials across so it
                # still starts out fully working, independently.
                old_id = new_conn.get("id")
                new_conn["id"] = uuid.uuid4().hex
                if old_id:
                    db_pw = self._resolve_password(old_id, "db")
                    if db_pw:
                        credential_store.set_password(new_conn["id"], "db", db_pw)
                        self._resolved_passwords[(new_conn["id"], "db")] = db_pw
                    ssh = new_conn.get("ssh_tunnel")
                    if ssh and ssh.get("enabled") and not ssh.get("use_key"):
                        ssh_pw = self._resolve_password(old_id, "ssh")
                        if ssh_pw:
                            credential_store.set_password(new_conn["id"], "ssh", ssh_pw)
                            self._resolved_passwords[(new_conn["id"], "ssh")] = ssh_pw

                self.connections.append(new_conn)
                self.save_connections()
                self.load_connections()

    def on_tree_item_clicked(self, item, column):
        """Load connection form when a connection item (not a group) is clicked."""
        if item.parent() is not None:
            self.load_selected_connection()

    def _get_selected_conn_item(self):
        """Return the currently selected connection QTreeWidgetItem, or None."""
        items = self.connection_tree.selectedItems()
        if not items:
            return None
        item = items[0]
        return item if item.parent() is not None else None

    def _find_tree_item(self, idx):
        """Walk the tree and return the leaf item whose UserRole data equals
        idx, or None. Shared by _select_connection_by_index and the
        sidebar row's "⋮" menu button (_show_row_menu)."""
        for gi in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(gi)
            for ci in range(group_item.childCount()):
                child = group_item.child(ci)
                if child.data(0, Qt.UserRole) == idx:
                    return child
        return None

    def _select_connection_by_index(self, idx):
        item = self._find_tree_item(idx)
        if item is not None:
            self.connection_tree.setCurrentItem(item)
            self.load_selected_connection()

    def _show_row_menu(self, conn_idx, _anchor=None):
        """Open the same menu as a right-click on this row (_on_tree_context_
        menu), triggered from the row's "⋮" button instead — it hit-tests
        by position, so just feed it that item's position."""
        item = self._find_tree_item(conn_idx)
        if item is not None:
            self._on_tree_context_menu(self.connection_tree.visualItemRect(item).center())

    def _toggle_favorite(self, conn_idx: int):
        if not (0 <= conn_idx < len(self.connections)):
            return
        self.connections[conn_idx]["favorite"] = not self.connections[conn_idx].get("favorite", False)
        self.save_connections()
        self.load_connections()

    def _show_import_not_available(self):
        """Placeholder — see ui/welcome_screen.py's identically-scoped
        button; no backing feature yet (GitHub issue #348)."""
        QMessageBox.information(
            self,
            "Import Connections",
            "Importing connections from other tools isn't available yet.\n\n"
            "It's on the roadmap — tracked as a GitHub issue.",
        )

    def _focus_search(self):
        self.connection_search.setFocus()
        self.connection_search.selectAll()

    # ── DB type change ───────────────────────────────────────────

    def _on_port_edited_by_user(self, _text: str):
        self._port_edited_by_user = True

    def on_type_changed(self, db_type):
        if db_type == "SQLite":
            self.host_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.user_input.setEnabled(False)
            self.password_input.setEnabled(False)
            self.database_input.setPlaceholderText("Path to .db file")
            self.ssh_enabled_check.setEnabled(False)
        else:
            self.host_input.setEnabled(True)
            self.port_input.setEnabled(True)
            self.user_input.setEnabled(True)
            self.password_input.setEnabled(True)
            self.database_input.setPlaceholderText("Database name (optional)")
            self.ssh_enabled_check.setEnabled(True)
            # Don't clobber a port the user (or load_selected_connection)
            # already put there — only auto-fill the dialect default while
            # the field still holds a previous auto-filled value (issue: a
            # custom port typed before picking the DB type from the
            # dropdown was silently overwritten back to 5432/3306).
            if not self._port_edited_by_user:
                if db_type == "MySQL":
                    self.port_input.setText("3306")
                elif db_type == "PostgreSQL":
                    self.port_input.setText("5432")

    def _on_environment_changed(self, index: int):
        """Auto-suggest Read-only when the user picks Staging/Production —
        one-directional (never auto-unchecks), so a deliberate choice to
        keep a Staging/Production connection writable is never overridden."""
        env = environment.ENVIRONMENTS[index]
        if env in (environment.STAGING, environment.PRODUCTION):
            self.read_only_check.setChecked(True)

    # ── SSH helpers ──────────────────────────────────────────────

    def on_ssh_enabled_toggled(self, checked):
        self.ssh_section.setVisible(checked)
        # Resize dialog to compact or expanded height
        QTimer.singleShot(0, lambda: self.resize(
            self.width(), self._expanded_height if checked else self._compact_height
        ))

    def set_ssh_fields_enabled(self, enabled):
        self.ssh_enabled_check.setChecked(enabled)
        self.ssh_section.setVisible(enabled)

    def browse_ssh_key(self):
        home_dir = os.path.expanduser("~/.ssh")
        if not os.path.exists(home_dir):
            home_dir = os.path.expanduser("~")
        file_path, _ = QFileDialog.getOpenFileName(self, "Select SSH Private Key", home_dir, "All Files (*)")
        if file_path:
            self.ssh_key_path_input.setText(file_path)

    def on_ssh_key_checkbox_changed(self, state):
        use_key = (state == 2)
        self.ssh_password_input.setEnabled(not use_key)
        self.ssh_password_visible_btn.setEnabled(not use_key)
        self.ssh_key_path_input.setEnabled(use_key)
        self.ssh_key_browse_btn.setEnabled(use_key)

    def _fit_field_to_content(self, edit: QLineEdit):
        """Size *edit* to its own text, not the row's full width: stays at
        the medium floor for short values, grows only as far as long values
        (e.g. a long RDS hostname) actually need, capped at `_FIT_CAP_WIDTH`."""
        needed = edit.fontMetrics().horizontalAdvance(edit.text()) + 24
        width = max(self.MEDIUM_FIELD_WIDTH, min(needed, self._FIT_CAP_WIDTH))
        edit.setMinimumWidth(width)
        edit.setMaximumWidth(width)

    def toggle_password_visibility(self):
        if self.password_input.echoMode() == QLineEdit.Password:
            self.password_input.setEchoMode(QLineEdit.Normal)
            self.password_visible_btn.setText("🚫")
        else:
            self.password_input.setEchoMode(QLineEdit.Password)
            self.password_visible_btn.setText("👁️")

    def toggle_ssh_password_visibility(self):
        if self.ssh_password_input.echoMode() == QLineEdit.Password:
            self.ssh_password_input.setEchoMode(QLineEdit.Normal)
            self.ssh_password_visible_btn.setText("🚫")
        else:
            self.ssh_password_input.setEchoMode(QLineEdit.Password)
            self.ssh_password_visible_btn.setText("👁️")

    # ── Load / save connections ──────────────────────────────────

    @classmethod
    def _sanitize_connection_entry(cls, raw) -> dict | None:
        """Validate one connections.json entry before any downstream code
        (credential_store, environment.normalize, db_service, the tree
        builder — several of which call .strip()/.get() assuming a plain
        dict of strings) touches it. Returns a cleaned copy, or None if the
        entry is malformed enough that it can't be trusted — issue #116:
        a hand-edited or malicious connections.json must not crash the app
        or feed unexpected types into connection logic; a bad profile is
        skipped, not fatal."""
        if not isinstance(raw, dict):
            return None
        conn = dict(raw)

        def _bounded_str(value):
            return value[:cls._MAX_STRING_LEN] if isinstance(value, str) else None

        name = _bounded_str(conn.get("name"))
        if not name:
            return None
        conn["name"] = name

        conn_id = conn.get("id")
        if conn_id is not None:
            # A non-string id would silently break credential_store lookups
            # keyed on it (get_password/set_password expect a string key).
            conn_id = _bounded_str(conn_id)
            if conn_id is None:
                return None
            conn["id"] = conn_id

        conn_type = conn.get("type", "mysql")
        if not isinstance(conn_type, str) or conn_type not in cls._ALLOWED_TYPES:
            return None
        conn["type"] = conn_type

        for key in ("host", "database", "user", "group", "password"):
            if key in conn:
                v = _bounded_str(conn[key])
                if v is None:
                    return None
                conn[key] = v

        if "port" in conn and conn["port"] is not None:
            try:
                port = int(conn["port"])
            except (TypeError, ValueError):
                return None
            if not (0 < port <= 65535):
                return None
            conn["port"] = port

        if "read_only" in conn and not isinstance(conn["read_only"], bool):
            conn["read_only"] = bool(conn["read_only"])

        if "favorite" in conn and not isinstance(conn["favorite"], bool):
            conn["favorite"] = bool(conn["favorite"])

        if "environment" in conn and not isinstance(conn["environment"], str):
            conn["environment"] = environment.DEFAULT_ENVIRONMENT

        ssh = conn.get("ssh_tunnel")
        if ssh is not None:
            if not isinstance(ssh, dict):
                conn.pop("ssh_tunnel", None)
            else:
                ssh = dict(ssh)
                for key in ("host", "user", "password", "key_path", "passphrase"):
                    if key in ssh:
                        v = _bounded_str(ssh[key])
                        if v is None:
                            return None
                        ssh[key] = v
                if "port" in ssh and ssh["port"] is not None:
                    try:
                        ssh_port = int(ssh["port"])
                    except (TypeError, ValueError):
                        return None
                    if not (0 < ssh_port <= 65535):
                        return None
                    ssh["port"] = ssh_port
                for key in ("enabled", "use_key"):
                    if key in ssh and not isinstance(ssh[key], bool):
                        ssh[key] = bool(ssh[key])
                conn["ssh_tunnel"] = ssh

        return conn

    @staticmethod
    def load_connection_by_id(conn_id: str):
        """Read connections.json fresh from disk and return the full,
        credential-resolved config for *conn_id* (or None if it no longer
        exists). Used by an already-open ConnectionPanel's Reconnect action
        so edits made in the Connection Manager while the tab stayed open
        (host, port, credentials, ...) take effect without having to close
        and reopen the tab (GitHub issue #17) — reconnecting otherwise
        reuses whatever config was captured when the tab was first opened."""
        if not conn_id or not os.path.exists(ConnectionDialog.CONNECTION_FILE):
            return None
        try:
            with open(ConnectionDialog.CONNECTION_FILE, "r") as f:
                connections = json.load(f)
        except Exception:
            return None
        if not isinstance(connections, list):
            return None

        for raw in connections:
            if not isinstance(raw, dict) or raw.get("id") != conn_id:
                continue
            conn = ConnectionDialog._sanitize_connection_entry(raw)
            if conn is None:
                return None
            conn["password"] = credential_store.get_password(conn_id, "db")
            ssh = conn.get("ssh_tunnel")
            if ssh and ssh.get("enabled") and not ssh.get("use_key"):
                ssh = dict(ssh)
                ssh["password"] = credential_store.get_password(conn_id, "ssh")
                conn["ssh_tunnel"] = ssh
            return conn
        return None

    @staticmethod
    def _restrict_permissions(path: str):
        """connections.json can hold plaintext passwords (the OS-keychain
        fallback — see SECURITY.md) alongside the always-plaintext host/user
        fields, so cap it to owner-only on POSIX (issue #116). Windows ACLs
        aren't touched — os.chmod's POSIX-mode-bit semantics don't map onto
        them the way this needs."""
        if os.name != "posix":
            return
        try:
            os.chmod(path, 0o600)
        except OSError as ex:
            logger.warning(f"Failed to restrict permissions on {path}: {ex}")

    def _migrate_legacy_connections(self):
        if os.path.exists(self.CONNECTION_FILE):
            return
        for legacy in [
            os.path.join(os.path.expanduser("~"), "connections.json"),
            os.path.join(os.getcwd(), "connections.json"),
        ]:
            if os.path.exists(legacy):
                os.makedirs(os.path.dirname(self.CONNECTION_FILE), exist_ok=True)
                import shutil
                shutil.copy2(legacy, self.CONNECTION_FILE)
                self._restrict_permissions(self.CONNECTION_FILE)
                break

    def _build_first_run_hint(self) -> QWidget:
        box = QWidget()
        box.setStyleSheet(
            "QWidget { background: #1c2733; border: 1px solid #2f3f4f; border-radius: 6px; }"
        )
        outer = QVBoxLayout(box)
        outer.setContentsMargins(10, 10, 10, 8)
        outer.setSpacing(6)

        header_row = QHBoxLayout()
        title = QLabel("Once you're connected")
        title.setStyleSheet("font-size: 12px; font-weight: bold; color: #9fc9ff; border: none;")
        header_row.addWidget(title)
        header_row.addStretch()
        dismiss_btn = QPushButton("✕")
        dismiss_btn.setFixedSize(18, 18)
        dismiss_btn.setToolTip("Dismiss")
        dismiss_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #8e8e93; font-size: 12px; }"
            "QPushButton:hover { color: #e5e5ea; }"
        )
        dismiss_btn.clicked.connect(self._dismiss_first_run_hint)
        header_row.addWidget(dismiss_btn)
        outer.addLayout(header_row)

        body = QLabel(
            "🔌  SSH tunnel support for remote databases\n"
            "▶  Schema-aware SQL autocomplete\n"
            "🔍  Schema Compare & Query Verifier"
        )
        body.setStyleSheet("font-size: 11px; color: #c7c7cc; border: none;")
        body.setWordWrap(True)
        outer.addWidget(body)

        return box

    def _dismiss_first_run_hint(self):
        onboarding.dismiss_connection_hint()
        self._update_first_run_hint_visibility()

    def _update_first_run_hint_visibility(self):
        show = not self.connections and not onboarding.is_connection_hint_dismissed()
        self._first_run_hint.setVisible(show)

    # Available width for the name/subtitle column of a sidebar row —
    # left_panel's fixed width, minus its layout margins, the tree's own
    # frame, the row's margins, and its icon/star/"⋮" siblings — so a long
    # connection name/host/group can't grow a row wide enough to push
    # those buttons out of the visible cell (QLabel doesn't elide on its
    # own, and setItemWidget's cell doesn't shrink children below their
    # layout's natural size).
    _ROW_TEXT_MAX_WIDTH = 165

    @staticmethod
    def _elide(text: str, pixel_size: int, bold: bool = False,
               max_width: int = None) -> str:
        """Pixel-accurate eliding (QFontMetrics), not a character-count
        guess — the latter fell over as soon as font size/weight or the
        panel width changed: a string well under the char budget still
        overflowed its pixel budget and got hard-clipped (no "…") instead
        of the layout just showing less of it."""
        font = QFont()
        font.setPixelSize(pixel_size)
        font.setBold(bold)
        width = ConnectionDialog._ROW_TEXT_MAX_WIDTH if max_width is None else max_width
        return QFontMetrics(font).elidedText(text, Qt.ElideRight, width)

    def _build_connection_row_widget(self, conn_idx: int, conn: dict) -> QWidget:
        """The visible content of one sidebar connection row — a DB icon,
        name + subtitle, a favorite star, and a "⋮" menu button. Column 0's
        own item text is left empty and unused for display (see
        load_connections's comment) — this widget is the only thing shown."""
        T = ThemeManager
        row = QWidget()
        self._style_connection_row(row, selected=False)
        row.setCursor(Qt.PointingHandCursor)
        row.mousePressEvent = lambda _event, idx=conn_idx: self._select_connection_by_index(idx)

        db_type = conn.get("type", "mysql")
        host = conn.get("host", "")
        row.setToolTip(f"{self._TYPE_LABELS.get(db_type, db_type.upper())} — {host}")

        h = QHBoxLayout(row)
        h.setContentsMargins(6, 7, 6, 7)
        h.setSpacing(10)

        icon_lbl = QLabel(self._TYPE_ICONS.get(db_type, "\U0001F5C4"))
        icon_lbl.setStyleSheet("background: transparent; font-size: 17px;")
        h.addWidget(icon_lbl)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        name_lbl = QLabel(self._elide(conn.get("name", ""), 14, bold=True))
        name_lbl.setStyleSheet(f"background: transparent; color: {T.D_TEXT}; font-size: 13.5px; font-weight: 700;")
        text_col.addWidget(name_lbl)

        subtitle_bits = []
        if host:
            subtitle_bits.append(host)
        if conn.get("read_only"):
            subtitle_bits.append("\U0001F512")
        subtitle_lbl = QLabel(self._elide("  ·  ".join(subtitle_bits), 12))
        subtitle_lbl.setStyleSheet(f"background: transparent; color: {T.D_TEXT3}; font-size: 11.5px;")
        text_col.addWidget(subtitle_lbl)
        h.addLayout(text_col, 1)

        is_favorite = bool(conn.get("favorite"))
        star_btn = QPushButton("★" if is_favorite else "☆")
        star_btn.setFixedSize(24, 24)
        star_btn.setCursor(Qt.PointingHandCursor)
        star_color = "#E0A23D" if is_favorite else T.D_TEXT3
        star_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; padding: 0; color: {star_color}; font-size: 15px; }}"
        )
        star_btn.setToolTip("Remove from Favorites" if is_favorite else "Add to Favorites")
        star_btn.clicked.connect(lambda _checked, idx=conn_idx: self._toggle_favorite(idx))
        h.addWidget(star_btn)

        menu_btn = QPushButton("⋮")
        menu_btn.setFixedSize(18, 24)
        menu_btn.setCursor(Qt.PointingHandCursor)
        menu_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; border: none; padding: 0; color: {T.D_TEXT3}; font-size: 14px; }}"
        )
        menu_btn.clicked.connect(lambda _checked, idx=conn_idx: self._show_row_menu(idx))
        h.addWidget(menu_btn)

        return row

    def _build_group_header_widget(self, group_item: QTreeWidgetItem, group_name: str, count: int) -> QWidget:
        """A clickable header row for one connection group — a chevron
        (kept in sync with expand/collapse via _on_tree_group_expansion_
        changed), the group name, and its connection count. A custom
        widget rather than the tree's own native branch/text rendering for
        the same reason leaf rows are (see _build_connection_row_widget):
        with setIndentation(0) there's no native decoration left to draw
        a chevron with, and this keeps the whole sidebar on one rendering
        approach instead of two."""
        T = ThemeManager
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        row.setCursor(Qt.PointingHandCursor)
        row.mousePressEvent = lambda _event, gi=group_item: self._toggle_group_expansion(gi)

        h = QHBoxLayout(row)
        h.setContentsMargins(4, 10, 6, 4)
        h.setSpacing(6)

        chevron = QLabel("⌄" if group_item.isExpanded() else "›")
        chevron.setFixedWidth(14)
        chevron.setStyleSheet(f"background: transparent; color: {T.D_TEXT2}; font-size: 13px; font-weight: 700;")
        h.addWidget(chevron)

        name_lbl = QLabel(self._elide(group_name, 14, bold=True, max_width=190))
        name_lbl.setStyleSheet(f"background: transparent; color: {T.D_TEXT2}; font-size: 13.5px; font-weight: 700;")
        h.addWidget(name_lbl, 1)

        count_lbl = QLabel(str(count))
        count_lbl.setStyleSheet(f"background: transparent; color: {T.D_TEXT3}; font-size: 12px;")
        count_lbl.setMinimumWidth(14)
        count_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(count_lbl, 0)

        row.chevron_label = chevron
        return row

    def _on_tree_group_expansion_changed(self, item: QTreeWidgetItem):
        if item.parent() is not None:
            return
        w = self.connection_tree.itemWidget(item, 0)
        if w is not None:
            w.chevron_label.setText("⌄" if item.isExpanded() else "›")
        # Collapsing is animated up front by _animate_group_collapse (it
        # has to run *before* the item actually collapses, since the rows
        # are simply gone once isExpanded() is False); expanding has no
        # such ordering problem, so it's handled here, uniformly, for
        # every path that expands a group (header click, auto-expand on
        # load, a search match, ...).
        if item.isExpanded():
            self._fade_in_group_children(item)

    def _toggle_group_expansion(self, group_item: QTreeWidgetItem):
        """Expand/collapse with a quick opacity fade instead of the hard,
        instant show/hide QTreeWidget defaults to for setItemWidget rows.
        Qt's own setAnimated(True) slide-open animation only animates
        natively delegate-painted rows — it silently does nothing once a
        row has a real item widget on it, which is why toggling a group
        used to look like a jump-cut instead of a smooth open/close."""
        if group_item.isExpanded():
            self._animate_group_collapse(group_item)
        else:
            group_item.setExpanded(True)

    def _fade_in_group_children(self, group_item: QTreeWidgetItem):
        for i in range(group_item.childCount()):
            w = self.connection_tree.itemWidget(group_item.child(i), 0)
            if w is None:
                continue
            effect = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", w)
            anim.setDuration(160)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda w=w: self._clear_row_opacity_effect(w))
            self._active_row_animations.append(anim)
            anim.start(QAbstractAnimation.DeleteWhenStopped)

    def _animate_group_collapse(self, group_item: QTreeWidgetItem):
        widgets = [
            self.connection_tree.itemWidget(group_item.child(i), 0)
            for i in range(group_item.childCount())
        ]
        widgets = [w for w in widgets if w is not None]
        if not widgets:
            group_item.setExpanded(False)
            return

        group_anim = QParallelAnimationGroup(self)
        for w in widgets:
            effect = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity")
            anim.setDuration(140)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.setEasingCurve(QEasingCurve.InCubic)
            group_anim.addAnimation(anim)

        def _finish(gi=group_item, group_anim=group_anim):
            if group_anim in self._active_row_animations:
                self._active_row_animations.remove(group_anim)
            try:
                gi.setExpanded(False)
            except RuntimeError:
                pass  # tree was cleared/rebuilt while the fade was running

        group_anim.finished.connect(_finish)
        self._active_row_animations.append(group_anim)
        group_anim.start(QAbstractAnimation.DeleteWhenStopped)

    def _clear_row_opacity_effect(self, w: QWidget):
        try:
            w.setGraphicsEffect(None)
        except RuntimeError:
            pass  # widget was detached/deleted (tree reload) mid-fade

    def _style_connection_row(self, row: QWidget, selected: bool):
        T = ThemeManager
        if selected:
            row.setStyleSheet(f"background: {T._alpha(T.D_BLUE, '22')}; border-radius: 6px;")
        else:
            row.setStyleSheet("background: transparent;")

    def _on_tree_current_item_changed(self, current, previous):
        """A setItemWidget row suppresses the delegate's own paint entirely,
        including the QSS QTreeWidget::item:selected highlight, so "this is
        the connection currently loaded in the form" has to be drawn on the
        row widget by hand instead — this is the one signal every selection
        path (click, arrow keys, select_last_connection, a fresh reload)
        already funnels through via setCurrentItem."""
        if previous is not None and previous.parent() is not None:
            w = self.connection_tree.itemWidget(previous, 0)
            if w is not None:
                self._style_connection_row(w, selected=False)
        if current is not None and current.parent() is not None:
            w = self.connection_tree.itemWidget(current, 0)
            if w is not None:
                self._style_connection_row(w, selected=True)

    def _clear_connection_tree(self):
        """QTreeWidget.clear() removes items but is well known not to
        reliably clean up widgets set via setItemWidget() with them —
        leaving orphaned widgets rendered at their old, now-stale
        positions (visible as a stray colored block behind/beside
        unrelated rows) the next time this rebuilds the tree, e.g. after
        _toggle_favorite or a drag-to-reorder. Explicitly detach each
        leaf's row widget first. Signals are blocked for the duration —
        clear() drops the current item, and _on_tree_current_item_changed
        touching an item mid-teardown (already-deleted C++ object) is a
        crash waiting to happen, not just a wasted no-op call."""
        self.connection_tree.blockSignals(True)
        try:
            # Stop rather than let finish: a collapse fade's completion
            # callback calls setExpanded(False) on a group item that's
            # about to be deleted below. stop() doesn't emit finished(),
            # so that callback never fires for a reload that interrupts it.
            for anim in self._active_row_animations:
                anim.stop()
            self._active_row_animations.clear()
            for gi in range(self.connection_tree.topLevelItemCount()):
                group_item = self.connection_tree.topLevelItem(gi)
                self._detach_item_widget(group_item)
                for ci in range(group_item.childCount()):
                    self._detach_item_widget(group_item.child(ci))
            self.connection_tree.clear()
        finally:
            self.connection_tree.blockSignals(False)

    def _detach_item_widget(self, item: QTreeWidgetItem):
        w = self.connection_tree.itemWidget(item, 0)
        if w is not None:
            # removeItemWidget only detaches it — Qt explicitly does NOT
            # take ownership/delete it, hide it, or even unparent it —
            # without both calls below it keeps existing *and rendering*,
            # at its last position, as an orphaned child of the viewport
            # (visible as a stray colored block over/behind unrelated rows
            # the moment this rebuilds — hide() is what actually stops
            # that, synchronously; deleteLater() is just the eventual
            # cleanup).
            self.connection_tree.removeItemWidget(item, 0)
            w.hide()
            w.deleteLater()

    def load_connections(self):
        self._migrate_legacy_connections()
        self._clear_connection_tree()
        self.connections = []

        if not os.path.exists(self.CONNECTION_FILE):
            self._update_first_run_hint_visibility()
            self._apply_filters()
            return

        try:
            with open(self.CONNECTION_FILE, "r") as f:
                raw = json.load(f)
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))
            self._update_first_run_hint_visibility()
            self._apply_filters()
            return
        if not isinstance(raw, list):
            raw = []

        self.connections = []
        skipped = 0
        for entry in raw:
            sanitized = self._sanitize_connection_entry(entry)
            if sanitized is None:
                skipped += 1
                continue
            self.connections.append(sanitized)
        if skipped:
            logger.warning(
                f"Skipped {skipped} malformed connection profile(s) in {self.CONNECTION_FILE}"
            )

        self._resolve_credentials()

        # Build ordered group → [indices] mapping
        groups: dict[str, list[int]] = {}
        for idx, conn in enumerate(self.connections):
            group = (conn.get("group") or "Default").strip()
            groups.setdefault(group, []).append(idx)

        for group_name, indices in groups.items():
            group_item = QTreeWidgetItem(self.connection_tree)
            group_item.setData(0, Qt.UserRole, None)
            group_item.setData(0, Qt.UserRole + 1, group_name)
            # Group headers are not selectable
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
            group_item.setExpanded(True)
            self.connection_tree.setItemWidget(
                group_item, 0, self._build_group_header_widget(group_item, group_name, len(indices))
            )

            for conn_idx in indices:
                conn = self.connections[conn_idx]
                raw_type = conn.get("type", "mysql")
                db_type = self._TYPE_LABELS.get(raw_type, raw_type.upper())
                host = conn.get("host", "")
                suffix = f"  [{db_type}]" + (f"  {host}" if host else "")
                env = environment.normalize(conn.get("environment"))
                env_suffix = (
                    f"  · {environment.BADGE_LABELS[env]}"
                    if env != environment.UNCLASSIFIED else ""
                )
                read_only_suffix = "  🔒" if conn.get("read_only") else ""
                child = QTreeWidgetItem(group_item)
                # Column 0's own text is left empty — the row below
                # (setItemWidget) covers the cell instead, and a heavily
                # customized QTreeWidget::item stylesheet (ThemeManager)
                # was still painting this text underneath/behind that
                # widget rather than being fully suppressed by it, showing
                # as ghosted double text. The search string _apply_filters
                # matches against lives in UserRole + 1 instead.
                child.setData(0, Qt.UserRole, conn_idx)
                child.setData(0, Qt.UserRole + 1, conn["name"] + suffix + env_suffix + read_only_suffix)
                child.setToolTip(0, f"{db_type} — {host}")
                self.connection_tree.setItemWidget(
                    child, 0, self._build_connection_row_widget(conn_idx, conn)
                )

        # Refresh the group combo with all known group names
        self._populate_group_combo()
        self._update_first_run_hint_visibility()
        self._apply_filters()

    def _populate_group_combo(self):
        """Rebuild the group combo items from all saved connections."""
        seen_groups: list[str] = []
        for conn in getattr(self, 'connections', []):
            g = (conn.get("group") or "Default").strip()
            if g and g not in seen_groups:
                seen_groups.append(g)
        current_text = self.group_input.lineEdit().text()
        self.group_input.blockSignals(True)
        self.group_input.clear()
        self.group_input.addItems(seen_groups)
        self.group_input.lineEdit().setText(current_text)
        self.group_input.blockSignals(False)

    def _resolve_credentials(self):
        """Assign a stable id to every connection, migrating any legacy
        plaintext password found on disk into the keychain.

        Deliberately does NOT eagerly fetch existing keychain passwords here
        — with several saved profiles, doing that on every load/save/reorder
        meant one OS keychain access per profile per action. Instead, a
        password is resolved lazily, only for the connection the user
        actually selects (see `_resolve_password`), and cached for the life
        of this dialog."""
        needs_resave = False
        for conn in self.connections:
            if not conn.get("id"):
                conn["id"] = uuid.uuid4().hex
                needs_resave = True

            if "environment" not in conn:
                conn["environment"] = environment.DEFAULT_ENVIRONMENT
                needs_resave = True

            if "read_only" not in conn:
                # Explicitly False, never derived from environment — an
                # existing writable connection must never be silently
                # locked just because this field didn't exist yet.
                conn["read_only"] = False
                needs_resave = True

            conn_id = conn["id"]

            plaintext_pw = conn.get("password", "")
            if plaintext_pw:
                credential_store.set_password(conn_id, "db", plaintext_pw)
                self._resolved_passwords[(conn_id, "db")] = plaintext_pw
                needs_resave = True

            ssh = conn.get("ssh_tunnel")
            if ssh and ssh.get("enabled") and not ssh.get("use_key"):
                plaintext_ssh_pw = ssh.get("password", "")
                if plaintext_ssh_pw:
                    credential_store.set_password(conn_id, "ssh", plaintext_ssh_pw)
                    self._resolved_passwords[(conn_id, "ssh")] = plaintext_ssh_pw
                    needs_resave = True

        if needs_resave:
            self.save_connections()

    def _resolve_password(self, conn_id: str, kind: str) -> str:
        """Fetch a single credential from the OS keychain on demand, caching
        the result for the lifetime of this dialog so repeated UI actions
        (saving, reordering, reselecting) don't re-hit the keychain for the
        same item."""
        key = (conn_id, kind)
        if key not in self._resolved_passwords:
            self._resolved_passwords[key] = credential_store.get_password(conn_id, kind)
        return self._resolved_passwords[key]

    def _remember_form_password(self, data: dict):
        """Record a just-submitted form's password(s) as this session's known
        value for that connection, so `save_connections()` knows it's safe to
        sync (rather than skipping a credential it never actually resolved)."""
        if "password" not in data:
            return
        self._resolved_passwords[(data["id"], "db")] = data["password"]
        ssh = data.get("ssh_tunnel")
        if ssh and ssh.get("enabled") and not ssh.get("use_key"):
            self._resolved_passwords[(data["id"], "ssh")] = ssh.get("password", "")

    def save_connections(self):
        os.makedirs(os.path.dirname(self.CONNECTION_FILE), exist_ok=True)
        sanitized = []
        keyring_failures = []
        for conn in self.connections:
            c = dict(conn)
            conn_id = c.get("id")
            c.pop("password", None)
            if conn_id:
                c["password"] = self._sync_password(conn_id, "db", c.get("name", conn_id), keyring_failures)

                ssh = c.get("ssh_tunnel")
                if ssh and ssh.get("enabled") and not ssh.get("use_key"):
                    ssh = dict(ssh)
                    ssh.pop("password", None)
                    ssh["password"] = self._sync_password(
                        conn_id, "ssh", f"{c.get('name', conn_id)} (SSH)", keyring_failures
                    )
                    c["ssh_tunnel"] = ssh
            sanitized.append(c)
        with open(self.CONNECTION_FILE, "w") as f:
            json.dump(sanitized, f, indent=4)
        self._restrict_permissions(self.CONNECTION_FILE)

        if keyring_failures:
            self._warn_keyring_unavailable(keyring_failures)

    def _sync_password(self, conn_id: str, kind: str, label: str, keyring_failures: list) -> str:
        """Write this dialog session's known value of a credential to the
        keychain, returning what belongs in connections.json (always blank
        on success, kept in plaintext if the keychain write failed).

        If this credential was never resolved or edited in this session
        (i.e. the user never selected that connection), its true current
        value is unknown here — skip touching the keychain entirely rather
        than risk deleting it based on an unresolved blank."""
        key = (conn_id, kind)
        if key not in self._resolved_passwords:
            return ""
        pw = self._resolved_passwords[key]
        if not pw:
            credential_store.delete_password(conn_id, kind)
            return ""
        if credential_store.set_password(conn_id, kind, pw):
            return ""
        keyring_failures.append(label)
        return pw

    def _warn_keyring_unavailable(self, names):
        QMessageBox.warning(
            self,
            "Password Not Stored Securely",
            "QForge could not reach the system keychain to store the "
            "password(s) for:\n\n"
            + "\n".join(f"  • {n}" for n in names)
            + "\n\nThe password(s) were kept in connections.json in plain "
            "text instead of being lost. This can happen after updating "
            "QForge, if a password was stored by a previous version and the "
            "keychain still associates it with that older install. Try "
            "opening Keychain Access, searching for \"QForge\", deleting the "
            "affected entries, then reopening the connection and saving it "
            "again. Otherwise, check that your OS keyring service is "
            "unlocked and running.",
        )

    def _get_group_value(self) -> str:
        """Return the typed group name — free text; any name not matching an
        existing group (see _populate_group_combo) is a new group."""
        return self.group_input.lineEdit().text().strip() or "Default"

    # ── Form helpers ─────────────────────────────────────────────

    def get_form_data(self, require_name=True):
        """require_name=False for actions that don't persist the connection
        (test, one-off connect) — a name is only mandatory once the details
        are actually being stored (Save, or auto-save on Connect for an
        already-saved connection). When omitted, falls back to host/database
        so the connection still has a readable label in the UI."""
        db_type = self.type_input.currentText().lower()
        name = self.name_input.text().strip()
        host = self.host_input.text().strip()
        database = self.database_input.text().strip()

        if not name:
            if require_name:
                raise ValueError("Connection name cannot be empty.")
            name = host or database or "Unnamed connection"

        data = {
            "type": db_type,
            "name": name,
            "environment": environment.ENVIRONMENTS[self.environment_input.currentIndex()],
            "read_only": self.read_only_check.isChecked(),
            "group": self._get_group_value(),
            "database": database,
        }

        port_text = self.port_input.text().strip()
        if not port_text.isdigit():
            raise ValueError(f"Port must be a number (got '{port_text}').")
        data["host"] = host
        data["port"] = int(port_text)
        data["user"] = self.user_input.text().strip()
        data["password"] = self.password_input.text()

        if self.ssh_enabled_check.isChecked():
            ssh_port_text = self.ssh_port_input.text().strip()
            if not ssh_port_text.isdigit():
                raise ValueError(f"SSH port must be a number (got '{ssh_port_text}').")
            data["ssh_tunnel"] = {
                "enabled": True,
                "host": self.ssh_host_input.text().strip(),
                "port": int(ssh_port_text),
                "user": self.ssh_user_input.text().strip(),
                "use_key": self.ssh_use_key_checkbox.isChecked(),
                "password": self.ssh_password_input.text() if not self.ssh_use_key_checkbox.isChecked() else "",
                "key_path": self.ssh_key_path_input.text() if self.ssh_use_key_checkbox.isChecked() else "",
            }
        else:
            data["ssh_tunnel"] = {"enabled": False}

        return data

    def clear_form(self):
        self._port_edited_by_user = False
        self.type_input.setCurrentIndex(0)
        self.name_input.clear()
        self.environment_input.setCurrentIndex(0)
        self.read_only_check.setChecked(False)
        self.group_input.clear()
        self.host_input.clear()
        self.port_input.setText("3306")
        self.database_input.clear()
        self.user_input.clear()
        self.password_input.clear()
        self.ssh_enabled_check.setChecked(False)
        self.ssh_host_input.clear()
        self.ssh_port_input.setText("22")
        self.ssh_user_input.clear()
        self.ssh_password_input.clear()
        self.ssh_use_key_checkbox.setChecked(False)
        self.ssh_key_path_input.clear()
        self.test_status_label.setText("")

    # ── Save (unified add / update) ──────────────────────────────

    def _flash_status(self, message: str, ok: bool = True):
        """Show a status pill next to the buttons that clears itself after a
        few seconds (issue #50: visible save confirmation). Reuses the same
        env-colored pill styling as the Test Connection result."""
        bg, text_color, _ = ThemeManager.env_colors(
            "local" if ok else "production", self._is_dark_theme()
        )
        self.test_status_label.setText(message)
        self.test_status_label.setStyleSheet(
            f"color: {text_color}; background: {bg}; padding: 6px 8px;"
            " border-radius: 4px; font-weight: 600;"
        )
        QTimer.singleShot(4000, lambda: self._clear_status_if_unchanged(message))

    def _clear_status_if_unchanged(self, message: str):
        if self.test_status_label.text() != message:
            return
        self.test_status_label.setText("")
        self.test_status_label.setStyleSheet("padding: 5px; border-radius: 3px;")

    def save_connection(self):
        selected_item = self._get_selected_conn_item()

        if selected_item is not None:
            # ── Update existing connection ───────────────────────
            conn_idx = selected_item.data(0, Qt.UserRole)
            try:
                data = self.get_form_data()
            except ValueError as ex:
                QMessageBox.warning(self, "Invalid Input", str(ex))
                return
            data["id"] = self.connections[conn_idx].get("id") or uuid.uuid4().hex
            self._remember_form_password(data)
            self.connections[conn_idx] = data
            try:
                self.save_connections()
            except OSError as ex:
                self._flash_status(f"✗ Save failed: {ex}", ok=False)
                return
            self.load_connections()
            self._select_connection_by_index(conn_idx)
            self._flash_status("✓ Connection updated successfully.")
            return

        # ── No connection selected: look up by details first ─────
        db_type = self.type_input.currentText().lower()
        host = self.host_input.text().strip()
        port = self.port_input.text().strip()
        user = self.user_input.text().strip()
        database = self.database_input.text().strip()

        for i, conn in enumerate(self.connections):
            if (conn.get("type") == db_type
                    and conn.get("host", "") == host
                    and str(conn.get("port", "")) == port
                    and conn.get("user", "") == user
                    and conn.get("database", "") == database):
                # Existing match — just select it
                self.load_connections()
                self._select_connection_by_index(i)
                return

        # ── Add as new connection (name required) ────────────────
        if not require_under_limit(
            Limit.MAX_CONNECTIONS, len(self.connections), "connections", self,
        ):
            return
        try:
            data = self.get_form_data()
        except ValueError as ex:
            QMessageBox.warning(self, "Invalid Input", str(ex))
            return
        data["id"] = uuid.uuid4().hex
        self._remember_form_password(data)
        self.connections.append(data)
        try:
            self.save_connections()
        except OSError as ex:
            self.connections.pop()
            self._flash_status(f"✗ Save failed: {ex}", ok=False)
            return
        self.load_connections()
        self._select_connection_by_index(len(self.connections) - 1)
        self._flash_status("✓ Connection created successfully.")

    def delete_connection(self):
        selected_item = self._get_selected_conn_item()
        if selected_item is None:
            return
        conn_idx = selected_item.data(0, Qt.UserRole)
        conn_id = self.connections[conn_idx].get("id", "")
        credential_store.delete_password(conn_id, "db")
        credential_store.delete_password(conn_id, "ssh")
        del self.connections[conn_idx]
        self.save_connections()
        self.load_connections()
        self.clear_form()

    def load_selected_connection(self):
        selected_item = self._get_selected_conn_item()
        if selected_item is None:
            return
        conn_idx = selected_item.data(0, Qt.UserRole)
        if conn_idx is None:
            return
        # Reset field colours when switching connections
        self._clear_field_colours()
        self.test_status_label.setText("")
        self.test_status_label.setStyleSheet("padding: 5px; border-radius: 3px;")

        connection = self.connections[conn_idx]
        db_type = connection.get("type", "mysql")
        type_map = {"mysql": "MySQL", "postgresql": "PostgreSQL", "sqlite": "SQLite"}
        # Reset before setCurrentText() below (which fires on_type_changed)
        # so a *previous* connection's manually-typed port doesn't leak into
        # this one and block its dialect-default fill.
        self._port_edited_by_user = False
        self.type_input.setCurrentText(type_map.get(db_type, "MySQL"))
        self.name_input.setText(connection["name"])
        env = environment.normalize(connection.get("environment"))
        self.environment_input.setCurrentText(environment.COMBO_LABELS[env])
        # Set explicitly (after the line above) so the stored value always
        # wins over _on_environment_changed's auto-suggest side effect.
        self.read_only_check.setChecked(connection.get("read_only", False))
        self.group_input.lineEdit().setText(connection.get("group", ""))
        self.database_input.setText(connection.get("database", ""))

        self.host_input.setText(connection.get("host", ""))
        self.port_input.setText(str(connection.get("port", 3306)))
        self.user_input.setText(connection.get("user", ""))
        resolved_pw = self._resolve_password(connection["id"], "db")
        connection["password"] = resolved_pw
        self.password_input.setText(resolved_pw)

        ssh_data = connection.get("ssh_tunnel", {"enabled": False})
        if ssh_data.get("enabled", False):
            self.ssh_enabled_check.setChecked(True)
            self.ssh_host_input.setText(ssh_data.get("host", ""))
            self.ssh_port_input.setText(str(ssh_data.get("port", 22)))
            self.ssh_user_input.setText(ssh_data.get("user", ""))
            use_key = ssh_data.get("use_key", False)
            self.ssh_use_key_checkbox.setChecked(use_key)
            if use_key:
                self.ssh_key_path_input.setText(ssh_data.get("key_path", ""))
            else:
                resolved_ssh_pw = self._resolve_password(connection["id"], "ssh")
                ssh_data["password"] = resolved_ssh_pw
                self.ssh_password_input.setText(resolved_ssh_pw)
        else:
            self.ssh_enabled_check.setChecked(False)

        # setText() leaves the cursor at the end of the string, which scrolls
        # long values so the START is hidden (e.g. "Adaptive Connection"
        # renders as "\daptive Connection" — issue #13). Reset the cursor so
        # the beginning of each value is visible by default.
        for line_edit in (
            self.name_input,
            self.group_input.lineEdit(),
            self.host_input,
            self.port_input,
            self.database_input,
            self.user_input,
            self.password_input,
            self.ssh_host_input,
            self.ssh_port_input,
            self.ssh_user_input,
            self.ssh_key_path_input,
            self.ssh_password_input,
        ):
            line_edit.setCursorPosition(0)

    def connect_selected(self):
        selected_item = self._get_selected_conn_item()

        try:
            data = self.get_form_data(require_name=(selected_item is not None))
        except ValueError as ex:
            QMessageBox.warning(self, "Invalid Input", str(ex))
            return

        if selected_item is None:
            # No saved connection selected — e.g. "New Connection" was filled
            # in directly and never saved. Connect with the form values as
            # entered, without writing anything to connections.json or the
            # keychain; Save remains the only way to keep it (issue #33).
            data["id"] = uuid.uuid4().hex
            self.selected_connection = data
            self.accept()
            return

        # Persist whatever is currently in the form (including a freshly
        # typed password) automatically, so Connect never requires a
        # separate Save click first.
        conn_idx = selected_item.data(0, Qt.UserRole)
        data["id"] = self.connections[conn_idx].get("id") or uuid.uuid4().hex
        self._remember_form_password(data)
        self.connections[conn_idx] = data
        self.save_connections()

        self.selected_connection = data
        self.save_last_connection(self.selected_connection)
        self._record_recent_connection(data["id"])
        self.accept()

    def get_selected_connection(self):
        return self.selected_connection

    def _record_recent_connection(self, conn_id: str):
        """Feeds the sidebar's "Recent" quick filter (see _apply_filters) —
        a small persisted MRU list, most-recent-first, capped at
        _MAX_RECENT. Only called for an actual saved profile (not a
        throwaway "New Connection" that was never saved) — see caller."""
        if not conn_id:
            return
        ids = preferences.get(self._RECENT_PREF_KEY, [])
        if not isinstance(ids, list):
            ids = []
        ids = [i for i in ids if i != conn_id]
        ids.insert(0, conn_id)
        preferences.set(self._RECENT_PREF_KEY, ids[:self._MAX_RECENT])

    # ── Test connection ──────────────────────────────────────────

    def _set_current_item_color(self, color: QColor):
        """Tint the selected connection tree item with the given color."""
        item = self._get_selected_conn_item()
        if item is not None:
            item.setForeground(0, color)

    def _is_dark_theme(self) -> bool:
        """Whether the app's active theme is dark. The status/field tint
        colors below need to pick a light- or dark-appropriate palette —
        hardcoding the dark one clashes badly with a light theme (issue #10)."""
        return getattr(self.parent(), "current_theme", "dark") == "dark"

    def _set_field_colours(self, success: bool):
        """Turn all connection form fields green (success) or red (failure) like TablePlus."""
        bg, text_color, border = ThemeManager.env_colors(
            "local" if success else "production", self._is_dark_theme()
        )
        style = (
            f"background: {bg}; color: {text_color}; "
            f"border: 1px solid {border}; border-radius: 4px; padding: 3px 6px;"
        )
        main_fields = [
            self.host_input, self.port_input, self.database_input,
            self.user_input, self.password_input,
        ]
        ssh_fields = []
        if self.ssh_enabled_check.isChecked():
            ssh_fields = [
                self.ssh_host_input, self.ssh_port_input, self.ssh_user_input,
                self.ssh_password_input, self.ssh_key_path_input,
            ]
        for w in main_fields + ssh_fields:
            w.setStyleSheet(style)

    def _clear_field_colours(self):
        """Reset form fields to default stylesheet."""
        for w in (
            self.host_input, self.port_input, self.database_input,
            self.user_input, self.password_input,
            self.ssh_host_input, self.ssh_port_input, self.ssh_user_input,
            self.ssh_password_input, self.ssh_key_path_input,
        ):
            w.setStyleSheet("")

    def test_connection(self):
        from services.db_service import DbService
        try:
            data = self.get_form_data(require_name=False)
        except ValueError as ex:
            self.test_status_label.setText(f"⚠️ {ex}")
            self.test_status_label.setStyleSheet("color: orange; padding: 5px; border-radius: 3px;")
            return

        is_dark = self._is_dark_theme()
        testing_bg, testing_text, _ = ThemeManager.env_colors("development", is_dark)
        self.test_status_label.setText("⏳ Testing connection...")
        self.test_status_label.setStyleSheet(
            f"color: {testing_text}; background: {testing_bg}; padding: 5px; border-radius: 3px;"
        )
        self.test_btn.setEnabled(False)

        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        db_service = DbService()
        try:
            db_service.connect(data)
            db_service.disconnect()
            bg, text_color, _ = ThemeManager.env_colors("local", is_dark)
            self.test_status_label.setText("Connection successful")
            self.test_status_label.setStyleSheet(
                f"color: {text_color}; background: {bg}; padding: 6px 8px; border-radius: 4px;"
            )
            self._set_current_item_color(QColor("#30d158"))
            self._set_field_colours(True)
        except Exception as ex:
            bg, text_color, _ = ThemeManager.env_colors("production", is_dark)
            self.test_status_label.setText(f"Connection failed: {str(ex)}")
            self.test_status_label.setStyleSheet(
                f"color: {text_color}; background: {bg}; padding: 6px 8px; border-radius: 4px;"
            )
            self._set_current_item_color(QColor("#ff453a"))
            self._set_field_colours(False)
        finally:
            self.test_btn.setEnabled(True)

    # ── Search / filter ──────────────────────────────────────────

    def _build_quick_filter_row(self, key: str, icon: str, label_text: str) -> QFrame:
        row = QFrame()
        row.setObjectName(f"quickFilter_{key}")
        row.setCursor(Qt.PointingHandCursor)
        row.mousePressEvent = lambda _event, k=key: self._set_quick_filter(k)

        h = QHBoxLayout(row)
        h.setContentsMargins(8, 6, 8, 6)
        h.setSpacing(8)
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("background: transparent; font-size: 12px;")
        h.addWidget(icon_lbl)
        text_lbl = QLabel(label_text)
        text_lbl.setStyleSheet("background: transparent; font-size: 12.5px; font-weight: 600;")
        h.addWidget(text_lbl, 1)
        count_lbl = QLabel("0")
        count_lbl.setStyleSheet(f"background: transparent; color: {ThemeManager.D_TEXT3}; font-size: 11.5px;")
        h.addWidget(count_lbl)
        row.count_label = count_lbl

        self._restyle_quick_filter_row(row, active=(key == self._quick_filter))
        return row

    def _restyle_quick_filter_row(self, row: QFrame, active: bool):
        T = ThemeManager
        name = row.objectName()
        if active:
            row.setStyleSheet(
                f"QFrame#{name} {{ background: {T._alpha(T.D_BLUE, '22')}; border-radius: 6px; }}"
            )
        else:
            row.setStyleSheet(
                f"QFrame#{name} {{ background: transparent; border-radius: 6px; }}"
                f"QFrame#{name}:hover {{ background: {T.D_HOVER}; }}"
            )

    def _set_quick_filter(self, key: str):
        if self._quick_filter == key:
            return
        self._quick_filter = key
        for k, row in self._quick_filter_rows.items():
            self._restyle_quick_filter_row(row, active=(k == key))
        self._apply_filters()

    def _update_quick_filter_counts(self):
        recent_ids = preferences.get(self._RECENT_PREF_KEY, [])
        if not isinstance(recent_ids, list):
            recent_ids = []
        counts = {
            "all": len(self.connections),
            "recent": sum(1 for c in self.connections if c.get("id") in recent_ids),
            "favorites": sum(1 for c in self.connections if c.get("favorite")),
        }
        for key, row in self._quick_filter_rows.items():
            row.count_label.setText(str(counts.get(key, 0)))

    def _apply_filters(self):
        """Combines the search box with the active quick filter (All/
        Recent/Favorites) to decide which tree rows are visible — the
        merged replacement for what used to be a search-only
        filter_connections(). No "Environments" filter and no per-row
        colour dot by design (see init_ui's comment)."""
        search_text = self.connection_search.text().lower().strip()
        recent_ids = preferences.get(self._RECENT_PREF_KEY, [])
        if not isinstance(recent_ids, list):
            recent_ids = []

        for gi in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(gi)
            group_has_visible = False
            for ci in range(group_item.childCount()):
                child = group_item.child(ci)
                searchable = (child.data(0, Qt.UserRole + 1) or "").lower()
                visible = not search_text or search_text in searchable
                if visible and self._quick_filter != "all":
                    conn_idx = child.data(0, Qt.UserRole)
                    conn = self.connections[conn_idx] if conn_idx is not None else {}
                    if self._quick_filter == "favorites":
                        visible = bool(conn.get("favorite"))
                    elif self._quick_filter == "recent":
                        visible = conn.get("id") in recent_ids
                child.setHidden(not visible)
                if visible:
                    group_has_visible = True
            group_item.setHidden(not group_has_visible)
            if group_has_visible:
                group_item.setExpanded(True)

        if search_text or self._quick_filter != "all":
            visible_items = self._visible_connection_items()
            if visible_items and self._get_selected_conn_item() not in visible_items:
                self.connection_tree.setCurrentItem(visible_items[0])
                self.load_selected_connection()

        self._update_quick_filter_counts()

    def _visible_connection_items(self):
        """Return connection leaf items that are currently shown, in display order."""
        items = []
        for gi in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(gi)
            if group_item.isHidden():
                continue
            for ci in range(group_item.childCount()):
                child = group_item.child(ci)
                if not child.isHidden():
                    items.append(child)
        return items

    def _move_connection_selection(self, delta):
        """Move the current connection selection up/down among visible items."""
        visible_items = self._visible_connection_items()
        if not visible_items:
            return
        current = self._get_selected_conn_item()
        if current in visible_items:
            new_index = (visible_items.index(current) + delta) % len(visible_items)
        else:
            new_index = 0
        self.connection_tree.setCurrentItem(visible_items[new_index])
        self.load_selected_connection()

    def eventFilter(self, obj, event):
        if obj is self.connection_search and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move_connection_selection(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self._get_selected_conn_item()
                if item is not None:
                    self.load_selected_connection()
                return True
        return super().eventFilter(obj, event)

    # ── Last connection persistence ──────────────────────────────

    def save_last_connection(self, connection):
        try:
            with open(self.LAST_CONNECTION_FILE, "w") as f:
                json.dump({"name": connection["name"]}, f)
        except Exception:
            pass

    def select_last_connection(self):
        try:
            if not os.path.exists(self.LAST_CONNECTION_FILE):
                return
            with open(self.LAST_CONNECTION_FILE, "r") as f:
                last_name = json.load(f).get("name")
            for gi in range(self.connection_tree.topLevelItemCount()):
                group_item = self.connection_tree.topLevelItem(gi)
                for ci in range(group_item.childCount()):
                    child = group_item.child(ci)
                    conn_idx = child.data(0, Qt.UserRole)
                    if conn_idx is not None and self.connections[conn_idx]["name"] == last_name:
                        self.connection_tree.setCurrentItem(child)
                        self.load_selected_connection()
                        return
        except Exception:
            pass
