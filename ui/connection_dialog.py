import json
import os

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
    QFileDialog
)
from PySide6.QtGui import QShortcut, QKeySequence, QFont, QColor
from PySide6.QtCore import Qt


class ConnectionDialog(QDialog):

    _APP_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "QForge")
    CONNECTION_FILE = os.path.join(_APP_DIR, "connections.json")
    LAST_CONNECTION_FILE = os.path.join(_APP_DIR, "last_connection.json")

    def __init__(self, auto_connect_last=False):
        super().__init__()

        self.selected_connection = None
        self.auto_connect_last = auto_connect_last

        self.setWindowTitle("Connection Manager")
        self._compact_height = 530
        self._expanded_height = 730
        self.resize(760, self._compact_height)
        self.setMinimumSize(700, 420)
        self.setSizeGripEnabled(True)

        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)

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
        left_panel.setFixedWidth(210)
        left_panel.setStyleSheet("QWidget { border-right: 1px solid #3a3a3c; }")
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        search_label = QLabel("🔍 Search Connections:")
        search_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.connection_search = QLineEdit()
        self.connection_search.setPlaceholderText("Type to filter connections...")
        self.connection_search.setStyleSheet("font-size: 12px; padding: 8px;")
        self.connection_search.textChanged.connect(self.filter_connections)

        left_layout.addWidget(search_label)
        left_layout.addWidget(self.connection_search)

        self.connection_tree = QTreeWidget()
        self.connection_tree.setHeaderHidden(True)
        self.connection_tree.setIndentation(20)
        self.connection_tree.setRootIsDecorated(True)
        self.connection_tree.setAnimated(True)
        left_layout.addWidget(self.connection_tree)

        new_conn_btn = QPushButton("+ New Connection")
        new_conn_btn.setToolTip("Clear form to create a new connection")
        new_conn_btn.setStyleSheet(
            "QPushButton { background: #2c2c2e; color: #e5e5ea; border: 1px solid #48484a;"
            " border-radius: 5px; padding: 6px 10px; font-size: 13px; }"
            "QPushButton:hover { background: #3a3a3c; border-color: #636366; }"
        )
        new_conn_btn.clicked.connect(self._new_connection)
        left_layout.addWidget(new_conn_btn)

        left_panel.setLayout(left_layout)
        layout.addWidget(left_panel)

        # ── RIGHT PANEL ─────────────────────────────────────────
        right_panel = QWidget()
        form_layout = QFormLayout()
        form_layout.setContentsMargins(20, 16, 20, 8)
        form_layout.setSpacing(8)
        form_layout.setHorizontalSpacing(12)
        self.type_input = QComboBox()
        self.type_input.addItems(["MySQL", "PostgreSQL", "SQLite"])
        self.type_input.currentTextChanged.connect(self.on_type_changed)

        self.name_input = QLineEdit()
        self.group_input = QLineEdit()
        self.group_input.setPlaceholderText("e.g. Production, Staging, Local…")
        self.host_input = QLineEdit()
        self.port_input = QLineEdit("3306")
        self.database_input = QLineEdit()
        self.user_input = QLineEdit()

        # Password with visibility toggle
        password_widget = QWidget()
        password_layout = QHBoxLayout(password_widget)
        password_layout.setContentsMargins(0, 0, 0, 0)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
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

        form_layout.addRow("Type", self.type_input)
        form_layout.addRow("Name", self.name_input)
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
        self.ssh_port_input = QLineEdit("22")
        self.ssh_user_input = QLineEdit()

        ssh_password_widget = QWidget()
        ssh_password_layout = QHBoxLayout(ssh_password_widget)
        ssh_password_layout.setContentsMargins(0, 0, 0, 0)
        self.ssh_password_input = QLineEdit()
        self.ssh_password_input.setEchoMode(QLineEdit.Password)
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

        self.ssh_use_key_checkbox = QCheckBox("Use SSH Key")
        self.ssh_use_key_checkbox.stateChanged.connect(self.on_ssh_key_checkbox_changed)

        self.ssh_key_path_input = QLineEdit()
        self.ssh_key_path_input.setPlaceholderText("Path to private key file (e.g., ~/.ssh/id_rsa)")
        self.ssh_key_path_input.setEnabled(False)

        self.ssh_key_browse_btn = QPushButton("📁")
        self.ssh_key_browse_btn.setFixedWidth(40)
        self.ssh_key_browse_btn.setEnabled(False)
        self.ssh_key_browse_btn.clicked.connect(self.browse_ssh_key)

        ssh_key_layout = QHBoxLayout()
        ssh_key_layout.addWidget(self.ssh_key_path_input)
        ssh_key_layout.addWidget(self.ssh_key_browse_btn)
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

    def _select_connection_by_index(self, idx):
        """Walk the tree and select the item whose UserRole data equals idx."""
        for gi in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(gi)
            for ci in range(group_item.childCount()):
                child = group_item.child(ci)
                if child.data(0, Qt.UserRole) == idx:
                    self.connection_tree.setCurrentItem(child)
                    self.load_selected_connection()
                    return

    # ── DB type change ───────────────────────────────────────────

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
            if db_type == "MySQL":
                self.port_input.setText("3306")
            elif db_type == "PostgreSQL":
                self.port_input.setText("5432")

    # ── SSH helpers ──────────────────────────────────────────────

    def on_ssh_enabled_toggled(self, checked):
        self.ssh_section.setVisible(checked)
        # Resize dialog to compact or expanded height
        from PySide6.QtCore import QTimer
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
                break

    def load_connections(self):
        self._migrate_legacy_connections()
        self.connection_tree.clear()
        self.connections = []

        if not os.path.exists(self.CONNECTION_FILE):
            return

        try:
            with open(self.CONNECTION_FILE, "r") as f:
                self.connections = json.load(f)
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))
            return

        # Build ordered group → [indices] mapping
        groups: dict[str, list[int]] = {}
        for idx, conn in enumerate(self.connections):
            group = (conn.get("group") or "Default").strip()
            groups.setdefault(group, []).append(idx)

        bold_font = QFont()
        bold_font.setBold(True)

        for group_name, indices in groups.items():
            group_item = QTreeWidgetItem(self.connection_tree)
            group_item.setText(0, group_name)
            group_item.setFont(0, bold_font)
            group_item.setData(0, Qt.UserRole, None)
            # Group headers are not selectable
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)
            group_item.setExpanded(True)

            for conn_idx in indices:
                conn = self.connections[conn_idx]
                _type_names = {"mysql": "MySQL", "postgresql": "PostgreSQL", "sqlite": "SQLite"}
                raw_type = conn.get("type", "mysql")
                db_type = _type_names.get(raw_type, raw_type.upper())
                host = conn.get("host", "")
                suffix = f"  [{db_type}]" + (f"  {host}" if host else "")
                child = QTreeWidgetItem(group_item)
                child.setText(0, conn["name"] + suffix)
                child.setData(0, Qt.UserRole, conn_idx)
                child.setToolTip(0, f"{db_type} — {host}")

    def save_connections(self):
        os.makedirs(os.path.dirname(self.CONNECTION_FILE), exist_ok=True)
        with open(self.CONNECTION_FILE, "w") as f:
            json.dump(self.connections, f, indent=4)

    # ── Form helpers ─────────────────────────────────────────────

    def get_form_data(self):
        db_type = self.type_input.currentText().lower()
        name = self.name_input.text().strip()

        if not name:
            raise ValueError("Connection name cannot be empty.")

        data = {
            "type": db_type,
            "name": name,
            "group": self.group_input.text().strip(),
            "database": self.database_input.text().strip(),
        }

        if db_type != "sqlite":
            port_text = self.port_input.text().strip()
            if not port_text.isdigit():
                raise ValueError(f"Port must be a number (got '{port_text}').")
            data["host"] = self.host_input.text().strip()
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
        self.type_input.setCurrentIndex(0)
        self.name_input.clear()
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
            self.connections[conn_idx] = data
            self.save_connections()
            self.load_connections()
            self._select_connection_by_index(conn_idx)
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
        try:
            data = self.get_form_data()
        except ValueError as ex:
            QMessageBox.warning(self, "Invalid Input", str(ex))
            return
        self.connections.append(data)
        self.save_connections()
        self.load_connections()
        self._select_connection_by_index(len(self.connections) - 1)

    def delete_connection(self):
        selected_item = self._get_selected_conn_item()
        if selected_item is None:
            return
        conn_idx = selected_item.data(0, Qt.UserRole)
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
        self.type_input.setCurrentText(type_map.get(db_type, "MySQL"))
        self.name_input.setText(connection["name"])
        self.group_input.setText(connection.get("group", ""))
        self.database_input.setText(connection.get("database", ""))

        if db_type != "sqlite":
            self.host_input.setText(connection.get("host", ""))
            self.port_input.setText(str(connection.get("port", 3306)))
            self.user_input.setText(connection.get("user", ""))
            self.password_input.setText(connection.get("password", ""))

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
                    self.ssh_password_input.setText(ssh_data.get("password", ""))
            else:
                self.ssh_enabled_check.setChecked(False)

    def connect_selected(self):
        selected_item = self._get_selected_conn_item()
        if selected_item is None:
            QMessageBox.warning(self, "Warning", "Select a connection first.")
            return
        conn_idx = selected_item.data(0, Qt.UserRole)
        self.selected_connection = self.connections[conn_idx]
        self.save_last_connection(self.selected_connection)
        self.accept()

    def get_selected_connection(self):
        return self.selected_connection

    # ── Test connection ──────────────────────────────────────────

    def _set_current_item_color(self, color: QColor):
        """Tint the selected connection tree item with the given color."""
        item = self._get_selected_conn_item()
        if item is not None:
            item.setForeground(0, color)

    def _set_field_colours(self, success: bool):
        """Turn all connection form fields green (success) or red (failure) like TablePlus."""
        if success:
            bg  = "#0d2e0d"
            border = "#30d158"
        else:
            bg  = "#2e0d0d"
            border = "#ff453a"
        style = f"background: {bg}; border: 1px solid {border}; border-radius: 4px; padding: 3px 6px;"
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
            data = self.get_form_data()
        except ValueError as ex:
            self.test_status_label.setText(f"⚠️ {ex}")
            self.test_status_label.setStyleSheet("color: orange; padding: 5px; border-radius: 3px;")
            return

        self.test_status_label.setText("⏳ Testing connection...")
        self.test_status_label.setStyleSheet("color: #0096FF; background: #2a2a2a; padding: 5px; border-radius: 3px;")
        self.test_btn.setEnabled(False)

        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()

        db_service = DbService()
        try:
            db_service.connect(data)
            db_service.disconnect()
            self.test_status_label.setText("Connection successful")
            self.test_status_label.setStyleSheet("color: #30d158; background: #0d2e0d; padding: 6px 8px; border-radius: 4px;")
            self._set_current_item_color(QColor("#30d158"))
            self._set_field_colours(True)
        except Exception as ex:
            self.test_status_label.setText(f"Connection failed: {str(ex)}")
            self.test_status_label.setStyleSheet("color: #ff453a; background: #2e0d0d; padding: 6px 8px; border-radius: 4px;")
            self._set_current_item_color(QColor("#ff453a"))
            self._set_field_colours(False)
        finally:
            self.test_btn.setEnabled(True)

    # ── Search / filter ──────────────────────────────────────────

    def filter_connections(self, search_text):
        search_text = search_text.lower().strip()
        for gi in range(self.connection_tree.topLevelItemCount()):
            group_item = self.connection_tree.topLevelItem(gi)
            group_has_visible = False
            for ci in range(group_item.childCount()):
                child = group_item.child(ci)
                visible = not search_text or search_text in child.text(0).lower()
                child.setHidden(not visible)
                if visible:
                    group_has_visible = True
            group_item.setHidden(bool(search_text) and not group_has_visible)

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
